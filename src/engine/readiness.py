from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.registry import CounterpartyRegistry
from engine.verification import (
    canonical_json,
    verify_ed25519_signature,
    verify_three_party_contract_signatures,
)


GATE_NAMES = (
    "VERIFIED_BUYER",
    "VERIFIED_SUPPLIER",
    "REAL_PURCHASE_ORDER_INVOICE",
    "VERIFIED_CAPITAL_PROVIDER",
    "SIGNED_TRI_PARTY_AGREEMENT",
    "FUNDING_AUTHORIZATION",
    "AUTHORIZED_SETTLEMENT_RAIL",
    "ACTUAL_TRANSACTION_PARAMETERS",
    "REPAYMENT_SETTLEMENT_TERMS",
    "M_FLOW_FEE_TREASURY_SET",
)


def _sha(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value)
    ).hexdigest()


def _is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str
    details: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "details": self.details,
        }


class SystemReadinessAuditor:
    """
    Fail-closed evaluator for the ten M-FLOW real-world dependencies.

    Secrets are NEVER included in the resulting readiness document.
    """

    def __init__(
        self,
        registry: CounterpartyRegistry | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:

        self.registry = registry or CounterpartyRegistry()

        self.manifest_path = Path(
            manifest_path
            or os.getenv(
                "M_FLOW_READINESS_FILE",
                "data/readiness.json",
            )
        )

    def _manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}

        raw = self.manifest_path.read_text(
            encoding="utf-8"
        ).strip()

        if not raw:
            return {}

        data = json.loads(raw)

        if not isinstance(data, dict):
            raise ValueError("READINESS_MANIFEST_INVALID")

        return data

    def _verified_entity(
        self,
        entity_type: str,
        entity_id: str | None,
    ) -> tuple[bool, dict[str, Any]]:

        rows = self.registry.verified(entity_type)

        if entity_id:
            rows = [
                row
                for row in rows
                if row.get("entityId") == entity_id
            ]

        if not rows:
            return False, {
                "entityType": entity_type,
                "entityId": entity_id,
                "registered": False,
            }

        row = rows[0]

        profile = row.get("profile") or {}

        if entity_type == "CAPITAL_PROVIDER":
            authorized = (
                profile.get("authorizationStatus") == "AUTHORIZED"
                or profile.get("liquidityVaultAuthorized") is True
            )

            if not authorized:
                return False, {
                    "entityType": entity_type,
                    "entityId": row.get("entityId"),
                    "registered": True,
                    "authorized": False,
                    "registrationHash": row.get("registrationHash"),
                }

        return True, {
            "entityType": entity_type,
            "entityId": row.get("entityId"),
            "registered": True,
            "authorized": True if entity_type == "CAPITAL_PROVIDER" else None,
            "registrationHash": row.get("registrationHash"),
        }

    def evaluate(self) -> dict[str, Any]:
        manifest = self._manifest()

        buyer_id = manifest.get("buyerId")
        supplier_id = manifest.get("supplierId")
        provider_id = manifest.get("capitalProviderId")

        # 1. VERIFIED_BUYER
        buyer_ok, buyer_detail = self._verified_entity(
            "BUYER",
            buyer_id,
        )

        # 2. VERIFIED_SUPPLIER
        supplier_ok, supplier_detail = self._verified_entity(
            "SUPPLIER",
            supplier_id,
        )

        # 4. VERIFIED_CAPITAL_PROVIDER
        provider_ok, provider_detail = self._verified_entity(
            "CAPITAL_PROVIDER",
            provider_id,
        )

        # 3. REAL_PURCHASE_ORDER_INVOICE
        po_hash = str(
            manifest.get("purchaseOrderSha256", "")
        ).lower()

        invoice_hash = str(
            manifest.get("invoiceSha256", "")
        ).lower()

        trade_doc_ok = (
            _is_sha(po_hash)
            and _is_sha(invoice_hash)
            and (
                self.registry.document_hash_registered(
                    po_hash,
                    supplier_id,
                )
                or self.registry.document_hash_registered(
                    po_hash,
                    buyer_id,
                )
            )
            and (
                self.registry.document_hash_registered(
                    invoice_hash,
                    supplier_id,
                )
                or self.registry.document_hash_registered(
                    invoice_hash,
                    buyer_id,
                )
            )
        )

        # 5. SIGNED_TRI_PARTY_AGREEMENT
        agreement_hash = str(
            manifest.get(
                "triPartyAgreementSha256",
                "",
            )
        ).lower()

        terms_hash = str(
            manifest.get("termsHash", "")
        ).lower()

        contract_signatures = (
            manifest.get("contractSignatures")
            or []
        )

        agreement_ok = False

        if (
            _is_sha(agreement_hash)
            and _is_sha(terms_hash)
            and isinstance(contract_signatures, list)
        ):
            try:
                signature_result = (
                    verify_three_party_contract_signatures(
                        terms_hash,
                        contract_signatures,
                    )
                )

                agreement_ok = (
                    bool(signature_result.get("verified"))
                    and manifest.get(
                        "agreementDocumentHash"
                    ) == agreement_hash
                )

            except (
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
            ):
                agreement_ok = False

        # 6. FUNDING_AUTHORIZATION
        funding = manifest.get(
            "fundingAuthorization"
        ) or {}

        funding_ok = False

        if isinstance(funding, dict):
            payload = funding.get("payload")
            signature = funding.get("signatureB64")
            public_key = funding.get("publicKeyB64")
            token_hash = str(
                funding.get("tokenSha256", "")
            ).lower()

            if (
                isinstance(payload, dict)
                and _is_sha(token_hash)
                and isinstance(signature, str)
                and isinstance(public_key, str)
            ):
                funding_ok = (
                    _sha(payload) == token_hash
                )

                if funding_ok:
                    try:
                        funding_ok = verify_ed25519_signature(
                            payload,
                            signature,
                            public_key,
                        )
                    except (
                        ValueError,
                        RuntimeError,
                    ):
                        funding_ok = False

        # 7. AUTHORIZED_SETTLEMENT_RAIL
        rail = str(
            manifest.get("settlementRail")
            or os.getenv(
                "M_FLOW_SETTLEMENT_RAIL",
                "",
            )
        ).lower()

        webhook_active = (
            os.getenv(
                "M_FLOW_SETTLEMENT_WEBHOOK_ACTIVE",
                "",
            ).lower()
            == "true"
        )

        live_credentials = (
            bool(
                os.getenv("PAYPAL_CLIENT_ID")
                and os.getenv("PAYPAL_CLIENT_SECRET")
                and os.getenv("PAYPAL_ENV") == "live"
            )
        )

        rail_ok = (
            rail == "paypal"
            and live_credentials
            and webhook_active
            and _env_present(
                "M_FLOW_SETTLEMENT_WEBHOOK_ID"
            )
        )

        # 8. ACTUAL_TRANSACTION_PARAMETERS
        transaction = (
            manifest.get("transactionParameters")
            or {}
        )

        transaction_hash = str(
            manifest.get(
                "transactionParametersSha256",
                "",
            )
        ).lower()

        transaction_ok = (
            isinstance(transaction, dict)
            and bool(transaction)
            and _is_sha(transaction_hash)
            and _sha(transaction) == transaction_hash
        )

        if transaction_ok:
            transaction_ok = (
                transaction.get("buyerId")
                == buyer_id
                and transaction.get("supplierId")
                == supplier_id
                and transaction.get(
                    "capitalProviderId"
                )
                == provider_id
            )

        # 9. REPAYMENT_SETTLEMENT_TERMS
        repayment = (
            manifest.get("repaymentTerms")
            or {}
        )

        repayment_hash = str(
            manifest.get(
                "repaymentTermsSha256",
                "",
            )
        ).lower()

        repayment_ok = (
            isinstance(repayment, dict)
            and bool(repayment)
            and _is_sha(repayment_hash)
            and _sha(repayment) == repayment_hash
            and bool(repayment.get("maturityDate"))
            and bool(repayment.get("yieldSplit"))
            and bool(repayment.get("escrowAccountLock"))
        )

        # 10. M_FLOW_FEE_TREASURY_SET
        treasury_destination = str(
            manifest.get("treasuryDestination")
            or os.getenv(
                "M_FLOW_FEE_TREASURY_DESTINATION",
                "",
            )
        ).strip()

        treasury_hash = str(
            manifest.get(
                "treasuryDestinationSha256",
                "",
            )
        ).lower()

        treasury_ok = (
            bool(treasury_destination)
            and _is_sha(treasury_hash)
            and _sha(treasury_destination)
            == treasury_hash
        )

        gates = [
            GateResult(
                "VERIFIED_BUYER",
                buyer_ok,
                "VERIFIED"
                if buyer_ok
                else "BUYER_NOT_VERIFIED",
                buyer_detail,
            ),
            GateResult(
                "VERIFIED_SUPPLIER",
                supplier_ok,
                "VERIFIED"
                if supplier_ok
                else "SUPPLIER_NOT_VERIFIED",
                supplier_detail,
            ),
            GateResult(
                "REAL_PURCHASE_ORDER_INVOICE",
                trade_doc_ok,
                "PO_AND_INVOICE_HASHES_VERIFIED"
                if trade_doc_ok
                else "SIGNED_PO_INVOICE_HASHES_MISSING_OR_UNREGISTERED",
                {
                    "purchaseOrderSha256": (
                        po_hash
                        if _is_sha(po_hash)
                        else None
                    ),
                    "invoiceSha256": (
                        invoice_hash
                        if _is_sha(invoice_hash)
                        else None
                    ),
                },
            ),
            GateResult(
                "VERIFIED_CAPITAL_PROVIDER",
                provider_ok,
                "VERIFIED_AND_AUTHORIZED"
                if provider_ok
                else "CAPITAL_PROVIDER_NOT_VERIFIED_OR_AUTHORIZED",
                provider_detail,
            ),
            GateResult(
                "SIGNED_TRI_PARTY_AGREEMENT",
                agreement_ok,
                "THREE_PARTY_SIGNATURES_VERIFIED"
                if agreement_ok
                else "TRI_PARTY_SIGNATURES_NOT_VERIFIED",
                {
                    "agreementSha256": (
                        agreement_hash
                        if _is_sha(agreement_hash)
                        else None
                    ),
                    "termsHash": (
                        terms_hash
                        if _is_sha(terms_hash)
                        else None
                    ),
                },
            ),
            GateResult(
                "FUNDING_AUTHORIZATION",
                funding_ok,
                "SIGNED_FUNDING_AUTHORIZATION_VERIFIED"
                if funding_ok
                else "FUNDING_AUTHORIZATION_MISSING_OR_INVALID",
                {
                    "tokenSha256": (
                        funding.get("tokenSha256")
                        if isinstance(funding, dict)
                        and _is_sha(
                            str(
                                funding.get(
                                    "tokenSha256",
                                    "",
                                )
                            )
                        )
                        else None
                    )
                },
            ),
            GateResult(
                "AUTHORIZED_SETTLEMENT_RAIL",
                rail_ok,
                "PAYPAL_LIVE_CREDENTIALS_AND_WEBHOOK_CONFIGURED"
                if rail_ok
                else "LIVE_RAIL_OR_WEBHOOK_NOT_READY",
                {
                    "rail": rail or None,
                    "credentialsPresent": live_credentials,
                    "webhookActive": webhook_active,
                    "webhookIdConfigured": _env_present(
                        "M_FLOW_SETTLEMENT_WEBHOOK_ID"
                    ),
                },
            ),
            GateResult(
                "ACTUAL_TRANSACTION_PARAMETERS",
                transaction_ok,
                "TRANSACTION_PARAMETERS_HASH_MATCH"
                if transaction_ok
                else "TRANSACTION_PARAMETERS_MISSING_OR_HASH_MISMATCH",
                {
                    "transactionParametersSha256": (
                        transaction_hash
                        if _is_sha(transaction_hash)
                        else None
                    )
                },
            ),
            GateResult(
                "REPAYMENT_SETTLEMENT_TERMS",
                repayment_ok,
                "REPAYMENT_TERMS_HASH_MATCH"
                if repayment_ok
                else "REPAYMENT_TERMS_MISSING_OR_HASH_MISMATCH",
                {
                    "repaymentTermsSha256": (
                        repayment_hash
                        if _is_sha(repayment_hash)
                        else None
                    )
                },
            ),
            GateResult(
                "M_FLOW_FEE_TREASURY_SET",
                treasury_ok,
                "TREASURY_DESTINATION_HASH_MATCH"
                if treasury_ok
                else "TREASURY_DESTINATION_MISSING_OR_HASH_MISMATCH",
                {
                    "treasuryDestinationConfigured": bool(
                        treasury_destination
                    ),
                    "treasuryDestinationSha256": (
                        treasury_hash
                        if _is_sha(treasury_hash)
                        else None
                    ),
                },
            ),
        ]

        ready = all(
            gate.passed
            for gate in gates
        )

        return {
            "ready": ready,
            "gateCount": len(gates),
            "passedCount": sum(
                gate.passed
                for gate in gates
            ),
            "failedGates": [
                gate.name
                for gate in gates
                if not gate.passed
            ],
            "gates": [
                gate.public()
                for gate in gates
            ],
            "evaluatedAt": datetime.now(
                timezone.utc
            ).isoformat().replace(
                "+00:00",
                "Z",
            ),
        }

    def assert_ready_for_mode(
        self,
        target_mode: str,
    ) -> dict[str, Any]:

        target = target_mode.upper()

        if target not in {"PAPER", "LIVE"}:
            raise ValueError("INVALID_MODE")

        report = self.evaluate()

        if not report["ready"]:
            raise PermissionError(
                "READINESS_GATES_FAILED:"
                + ",".join(
                    report["failedGates"]
                )
            )

        return report
