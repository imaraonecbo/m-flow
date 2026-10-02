from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from engine.documents import TradeTerms, canonical_json, generate_trade_documents
from engine.revenue import RevenueEngine, TreasuryAdapter
from engine.verification import verify_ed25519_signature, verify_three_party_contract_signatures, validate_invoice_metadata
from engine.registry import CounterpartyRegistry
from engine.readiness import SystemReadinessAuditor


CENT = Decimal("0.01")
BPS_DENOM = Decimal("10000")
DEFAULT_VELOCITY_TARGET = Decimal(os.getenv("M_FLOW_VELOCITY_TARGET_PER_DAY", "0.01"))
DEFAULT_MIN_RISK = int(os.getenv("M_FLOW_MIN_RISK_SCORE", "7000"))


def q2(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("INVALID_DECIMAL") from exc


def q6(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("INVALID_DECIMAL") from exc


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AppendOnlyHashLedger:
    """Tamper-evident, process-local append-only ledger for the HTTP service.

    Every entry commits to the prior entry hash. This is tamper-evident, not an
    independently hosted WORM/immutable storage guarantee.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            previous = self._entries[-1]["entryHash"] if self._entries else "0" * 64
            body = {
                "index": len(self._entries),
                "ts": now_iso(),
                "eventType": event_type,
                "payload": payload,
                "previousHash": previous,
            }
            entry_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
            entry = {**body, "entryHash": entry_hash}
            self._entries.append(entry)
            return entry

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._entries))

    def verify(self) -> bool:
        with self._lock:
            previous = "0" * 64
            for expected_index, entry in enumerate(self._entries):
                body = {k: entry[k] for k in ("index", "ts", "eventType", "payload", "previousHash")}
                if entry["index"] != expected_index or entry["previousHash"] != previous:
                    return False
                if hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest() != entry["entryHash"]:
                    return False
                previous = entry["entryHash"]
            return True

    def has(self, event_type: str, predicate: Optional[Callable[[dict[str, Any]], bool]] = None) -> bool:
        with self._lock:
            for entry in self._entries:
                if entry["eventType"] == event_type and (predicate is None or predicate(entry["payload"])):
                    return True
            return False


class PaymentRailAdapter(ABC):
    name = "abstract"

    @abstractmethod
    def execute(self, *, amount: Decimal, currency: str, recipient: str, idempotency_key: str, memo: str) -> dict[str, Any]:
        raise NotImplementedError


class MockPaperRailAdapter(PaymentRailAdapter):
    name = "paper-mock"

    def execute(self, *, amount: Decimal, currency: str, recipient: str, idempotency_key: str, memo: str) -> dict[str, Any]:
        tx_hash = hashlib.sha256(
            f"PAPER|{idempotency_key}|{amount}|{currency}|{recipient}|{memo}".encode("utf-8")
        ).hexdigest()
        return {
            "status": "SIMULATED",
            "simulated": True,
            "transactionHash": tx_hash,
            "externalReference": f"PAPER-{tx_hash[:24]}",
        }


class PayPalLiveRailAdapter(PaymentRailAdapter):
    name = "paypal-live"

    def __init__(self, *, client_id: str, client_secret: str, base_url: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")

    def _access_token(self) -> str:
        response = httpx.post(
            f"{self.base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
            timeout=20.0,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("LIVE_PAYPAL_TOKEN_MISSING")
        return token

    def execute(self, *, amount: Decimal, currency: str, recipient: str, idempotency_key: str, memo: str) -> dict[str, Any]:
        token = self._access_token()
        payload = {
            "sender_batch_header": {
                "sender_batch_id": idempotency_key,
                "email_subject": "M-FLOW authorized settlement",
                "email_message": memo,
            },
            "items": [{
                "recipient_type": "EMAIL",
                "amount": {"value": f"{amount:.2f}", "currency": currency.upper()},
                "receiver": recipient,
                "note": memo,
                "sender_item_id": idempotency_key,
            }],
        }
        response = httpx.post(
            f"{self.base_url}/v1/payments/payouts",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": idempotency_key,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "status": data.get("batch_header", {}).get("batch_status", "SUBMITTED"),
            "simulated": False,
            "transactionHash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            "externalReference": data.get("batch_header", {}).get("payout_batch_id"),
            "providerResponse": data,
        }


class PaperTreasuryAdapter(TreasuryAdapter):
    name = "paper-mock-treasury"

    def collect_platform_fee(self, *, opportunity_id: str, amount: Decimal, currency: str, authorization_hash: str) -> str:
        digest = hashlib.sha256(
            f"PAPER-TREASURY|{opportunity_id}|{amount}|{currency}|{authorization_hash}".encode("utf-8")
        ).hexdigest()
        return f"PAPER-TREASURY-{digest[:24]}"


class LiveTreasuryAdapter(TreasuryAdapter):
    """Live treasury adapter using the same PayPal payout rail.

    It is only constructed after the full LIVE gate has passed.
    """

    def __init__(self, rail: PayPalLiveRailAdapter, treasury_recipient: str) -> None:
        self.rail = rail
        self.treasury_recipient = treasury_recipient

    def collect_platform_fee(self, *, opportunity_id: str, amount: Decimal, currency: str, authorization_hash: str) -> str:
        result = self.rail.execute(
            amount=amount,
            currency=currency,
            recipient=self.treasury_recipient,
            idempotency_key=f"M-FLOW-FEE-{authorization_hash}",
            memo=f"M-FLOW platform fee for {opportunity_id}",
        )
        return str(result.get("externalReference") or "")


class TradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunityId: str = Field(min_length=1, max_length=128)
    capital: str
    revenue: str
    cogs: str
    financingCost: str = "0.00"
    durationDays: int = Field(gt=0, le=3650)
    marginThreshold: str = "0.05"
    currency: str = Field(min_length=3, max_length=3)
    settlementDate: str
    buyerId: str
    supplierId: str
    capitalProviderId: str
    counterparty: dict[str, Any]
    evidenceRecords: list[dict[str, Any]] = Field(min_length=1)
    contractSignatures: list[dict[str, str]] = Field(default_factory=list)
    treasuryDestination: str = Field(min_length=1, max_length=512)
    recipient: str = Field(min_length=1, max_length=512)
    rail: str = "paypal"
    volatilityFactor: int = Field(default=0, ge=0, le=10000)
    originationBps: int = Field(default=200, ge=0, le=10000)
    spreadBps: int = Field(default=0, ge=0, le=10000)
    invoice: Optional[dict[str, Any]] = None
    vendor: Optional[dict[str, Any]] = None


@dataclass
class OpportunityRecord:
    opportunity_id: str
    state: str
    score: dict[str, Any]
    terms_hash: str
    execution: Optional[dict[str, Any]] = None


class MFlowPipeline:
    def __init__(self, mode: Optional[str] = None) -> None:
        self.mode = (mode or os.getenv("M_FLOW_MODE", "DISCOVERY")).upper()
        if self.mode not in {"DISCOVERY", "PAPER", "LIVE"}:
            raise ValueError("INVALID_MODE")

        self.ledger = AppendOnlyHashLedger()
        self.records: dict[str, OpportunityRecord] = {}
        self.paper_rail = MockPaperRailAdapter()

        self.registry = CounterpartyRegistry()
        self.readiness = SystemReadinessAuditor(self.registry)

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in {"DISCOVERY", "PAPER", "LIVE"}:
            raise ValueError("INVALID_MODE")

        # Every transition into PAPER or LIVE requires ALL ten
        # real-world dependencies to be present and cryptographically valid.
        if mode in {"PAPER", "LIVE"}:
            self.readiness.assert_ready_for_mode(mode)

        self.mode = mode
        self.ledger.append("MODE_CHANGED", {"mode": mode, "actor": "operator"})

    def evaluate(self, req: TradeRequest) -> dict[str, Any]:
        currency = req.currency.upper()
        if not currency.isalpha() or len(currency) != 3:
            raise ValueError("INVALID_CURRENCY")

        capital = q2(req.capital)
        revenue = q2(req.revenue)
        cogs = q2(req.cogs)
        financing = q2(req.financingCost)
        margin_threshold = q6(req.marginThreshold)
        if capital <= 0 or revenue <= 0 or cogs < 0 or financing < 0 or margin_threshold < 0:
            raise ValueError("INVALID_FINANCIAL_INPUT")
        if revenue < cogs:
            raise ValueError("REVENUE_BELOW_COGS")

        gross_margin = revenue - cogs
        platform_fee_estimate = (capital * Decimal(req.originationBps) / BPS_DENOM).quantize(CENT, rounding=ROUND_HALF_UP)
        net_profit = revenue - cogs - financing - platform_fee_estimate
        capital_velocity = (net_profit / (capital * Decimal(req.durationDays))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        gross_margin_rate = (gross_margin / revenue).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        margin_pass = gross_margin_rate >= margin_threshold

        cp = req.counterparty
        if cp.get("identityStatus") != "VERIFIED":
            counterparty_score = 0
        else:
            base = int(cp.get("creditRiskScore", 0))
            velocity = q6(cp.get("historicalSettlementVelocity", "0"))
            debt_ratio = q6(cp.get("verifiedDebtRatio", "1"))
            invoice_age = int(cp.get("invoiceAgeDays", 0))
            if not 0 <= base <= 10000 or not 0 <= velocity <= 1 or not 0 <= debt_ratio <= 1 or invoice_age < 0:
                raise ValueError("COUNTERPARTY_RISK_INPUT_INVALID")
            age_penalty = min(Decimal(invoice_age) / Decimal(180), Decimal(1))
            adjusted = Decimal(base) + velocity * Decimal(1000) - age_penalty * Decimal(1500) - debt_ratio * Decimal(2000)
            counterparty_score = max(0, min(10000, int(adjusted.to_integral_value(rounding=ROUND_HALF_UP))))

        velocity_score = max(0, min(10000, int((capital_velocity / DEFAULT_VELOCITY_TARGET * Decimal(10000)).to_integral_value(rounding=ROUND_HALF_UP)))) if DEFAULT_VELOCITY_TARGET > 0 else 0
        risk_adjusted = (Decimal(velocity_score) * Decimal("0.4") + Decimal(counterparty_score) * Decimal("0.4") - Decimal(req.volatilityFactor) * Decimal("0.2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        score = {
            "grossMargin": str(gross_margin),
            "grossMarginRate": str(gross_margin_rate),
            "financingCost": str(financing),
            "platformFeeEstimate": str(platform_fee_estimate),
            "netProfit": str(net_profit),
            "capitalVelocity": str(capital_velocity),
            "velocityScore": velocity_score,
            "counterpartyScore": counterparty_score,
            "volatilityFactor": req.volatilityFactor,
            "riskAdjustedScore": str(risk_adjusted),
            "marginPass": margin_pass,
            "minimumRiskScore": DEFAULT_MIN_RISK,
        }
        return {
            "capital": capital,
            "revenue": revenue,
            "cogs": cogs,
            "financing": financing,
            "platformFeeEstimate": platform_fee_estimate,
            "netProfit": net_profit,
            "grossMargin": gross_margin,
            "score": score,
            "currency": currency,
        }

    def verify_evidence(self, req: TradeRequest) -> dict[str, Any]:
        cp = req.counterparty
        if cp.get("identityStatus") != "VERIFIED":
            raise ValueError("COUNTERPARTY_NOT_VERIFIED")
        expected = {str(e.get("contentSha256", "")).lower(): e for e in cp.get("evidence", []) if e.get("contentSha256")}
        if not expected:
            raise ValueError("COUNTERPARTY_EVIDENCE_REGISTRY_EMPTY")

        verified = []
        for evidence in req.evidenceRecords:
            raw_b64 = evidence.get("contentBase64")
            declared_hash = str(evidence.get("contentSha256", "")).lower()
            if not raw_b64 or len(declared_hash) != 64:
                raise ValueError("EVIDENCE_HASH_INPUT_INVALID")
            try:
                raw = base64.b64decode(raw_b64, validate=True)
            except Exception as exc:
                raise ValueError("EVIDENCE_BASE64_INVALID") from exc
            actual_hash = hashlib.sha256(raw).hexdigest()
            if actual_hash != declared_hash:
                raise ValueError("EVIDENCE_HASH_MISMATCH")
            if declared_hash not in expected:
                raise ValueError("EVIDENCE_NOT_REGISTERED_WITH_COUNTERPARTY")
            verify_ed25519_signature(evidence.get("payload", {}), evidence["signatureB64"], evidence["publicKeyB64"])
            verified.append({"type": str(evidence.get("type", "")).upper(), "sha256": declared_hash})

        types = {x["type"] for x in verified}
        if not (types & {"KYC", "AML", "IDENTITY"}):
            raise ValueError("IDENTITY_EVIDENCE_REQUIRED")
        if not (types & {"INVOICE", "CONTRACT", "PURCHASE_ORDER"}):
            raise ValueError("TRADE_EVIDENCE_REQUIRED")
        if req.invoice is not None and req.vendor is not None:
            validate_invoice_metadata(req.invoice, req.vendor)
        return {"verified": True, "evidence": verified, "riskScore": int(cp["creditRiskScore"])}

    def legal_lock(self, req: TradeRequest, evaluated: dict[str, Any]) -> tuple[dict[str, Any], str]:
        terms = TradeTerms(
            opportunity_id=req.opportunityId,
            amount=evaluated["capital"],
            margin=evaluated["grossMargin"],
            settlement_date=req.settlementDate,
            buyer_id=req.buyerId,
            supplier_id=req.supplierId,
            capital_provider_id=req.capitalProviderId,
            currency=evaluated["currency"],
            cogs=evaluated["cogs"],
            financing_cost=evaluated["financing"],
            platform_fee=evaluated["platformFeeEstimate"],
        )
        docs = generate_trade_documents(terms)
        payload = {
            "opportunityId": req.opportunityId,
            "termsHash": terms.terms_hash,
            "documentHashes": {kind: doc["sha256"] for kind, doc in docs.items()},
            "settlementDate": req.settlementDate,
        }
        self.ledger.append("LEGAL_TERMS_LOCKED", payload)
        self.ledger.append("DOCUMENT_GENERATED", {"opportunityId": req.opportunityId, "documentHashes": payload["documentHashes"]})
        return {"termsHash": terms.terms_hash, "documents": {k: {"sha256": v["sha256"], "markdown": v["markdown"]} for k, v in docs.items()}}, terms.terms_hash

    def _authorization_hash(self, req: TradeRequest, terms_hash: str, documents: dict[str, Any], fee_payload: dict[str, Any], signatures_verified: bool) -> str:
        return canonical_sha({
            "opportunityId": req.opportunityId,
            "termsHash": terms_hash,
            "documentHashes": {k: v["sha256"] for k, v in documents["documents"].items()},
            "fee": fee_payload,
            "contractsVerified": signatures_verified,
            "treasuryDestination": req.treasuryDestination,
        })

    def _set_state(self, opportunity_id: str, current: str, new: str) -> str:
        self.ledger.append("OPPORTUNITY_STATE_CHANGED", {"opportunityId": opportunity_id, "from": current, "to": new})
        return new

    def _live_credentials_ready(self) -> bool:
        return bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET") and os.getenv("PAYPAL_ENV") == "live")

    def _live_enabled(self) -> bool:
        return os.getenv("M_FLOW_LIVE_EXECUTION_ENABLED", "false").lower() == "true"

    def run(self, req: TradeRequest) -> dict[str, Any]:
        evaluated = self.evaluate(req)
        self.ledger.append("OPPORTUNITY_INGESTED", {"opportunityId": req.opportunityId, "mode": self.mode})
        self.ledger.append("SCORE_RECALCULATED", {"opportunityId": req.opportunityId, "score": evaluated["score"]})
        state = self._set_state(req.opportunityId, "NEW", "DISCOVERED")

        if self.mode == "DISCOVERY":
            self.records[req.opportunityId] = OpportunityRecord(req.opportunityId, state, evaluated["score"], "")
            self.ledger.append("EXECUTION_BLOCKED", {"opportunityId": req.opportunityId, "reason": "DISCOVERY_MODE"})
            raise HTTPException(status_code=400, detail={"code": "DISCOVERY_EXECUTION_BLOCKED", "state": state, "score": evaluated["score"]})

        evidence = self.verify_evidence(req)
        state = self._set_state(req.opportunityId, state, "EVIDENCE_VERIFIED")
        self.ledger.append("COUNTERPARTY_EVIDENCE_VERIFIED", {"opportunityId": req.opportunityId, **evidence})

        if not evaluated["score"]["marginPass"]:
            self.ledger.append("EXECUTION_BLOCKED", {"opportunityId": req.opportunityId, "reason": "MARGIN_THRESHOLD", "score": evaluated["score"]})
            raise HTTPException(status_code=422, detail={"code": "MARGIN_THRESHOLD_FAILED", "state": state, "score": evaluated["score"]})

        legal, terms_hash = self.legal_lock(req, evaluated)
        state = self._set_state(req.opportunityId, state, "LEGAL_LOCKED")

        contract_verified = False
        if req.contractSignatures:
            result = verify_three_party_contract_signatures(terms_hash, req.contractSignatures)
            contract_verified = bool(result["verified"])
            self.ledger.append("CONTRACT_SIGNATURES_VERIFIED", {"opportunityId": req.opportunityId, "termsHash": terms_hash, "roles": result["roles"]})
        else:
            self.ledger.append("CONTRACT_SIGNATURES_PENDING", {"opportunityId": req.opportunityId, "termsHash": terms_hash})

        origination_fee = (evaluated["capital"] * Decimal(req.originationBps) / BPS_DENOM).quantize(CENT, rounding=ROUND_HALF_UP)
        spread_income = (evaluated["netProfit"] * Decimal(req.spreadBps) / BPS_DENOM).quantize(CENT, rounding=ROUND_HALF_UP)
        total_platform_revenue = (origination_fee + spread_income).quantize(CENT, rounding=ROUND_HALF_UP)
        if total_platform_revenue <= 0:
            raise HTTPException(status_code=422, detail={"code": "REVENUE_NOT_POSITIVE", "state": state})
        fee_payload = {
            "dealVolume": str(evaluated["capital"]),
            "originationFee": str(origination_fee),
            "spreadIncome": str(spread_income),
            "totalPlatformRevenue": str(total_platform_revenue),
            "currency": evaluated["currency"],
            "originationBps": req.originationBps,
            "spreadBps": req.spreadBps,
        }

        if not req.treasuryDestination.strip():
            raise HTTPException(status_code=422, detail={"code": "TREASURY_DESTINATION_REQUIRED", "state": state})
        self.ledger.append("TREASURY_LOCKED", {"opportunityId": req.opportunityId, "destination": req.treasuryDestination, "amount": str(total_platform_revenue), "currency": evaluated["currency"]})
        state = self._set_state(req.opportunityId, state, "TREASURY_LOCKED")

        auth_hash = self._authorization_hash(req, terms_hash, legal, fee_payload, contract_verified)
        treasury = PaperTreasuryAdapter() if self.mode == "PAPER" else None
        revenue_engine = RevenueEngine(self.ledger.append, treasury=treasury)
        accrual = revenue_engine.calculate(
            opportunity_id=req.opportunityId,
            deal_volume=evaluated["capital"],
            net_yield=evaluated["netProfit"],
            origination_bps=req.originationBps,
            spread_bps=req.spreadBps,
            currency=evaluated["currency"],
            authorization_hash=auth_hash,
        )
        state = self._set_state(req.opportunityId, state, "FEE_ACCRUED")
        if self.mode == "LIVE":
            if not self._live_credentials_ready() or not self._live_enabled():
                self.ledger.append("EXECUTION_BLOCKED", {"opportunityId": req.opportunityId, "reason": "LIVE_CREDENTIALS_OR_ENABLEMENT"})
                raise HTTPException(status_code=403, detail={"code": "LIVE_GATE_PROVIDER_CREDENTIALS", "state": state})
            if not contract_verified:
                self.ledger.append("EXECUTION_BLOCKED", {"opportunityId": req.opportunityId, "reason": "LIVE_CONTRACT_SIGNATURES"})
                raise HTTPException(status_code=403, detail={"code": "LIVE_GATE_CONTRACT_SIGNATURES", "state": state})
            risk_score = int(evaluated["score"]["counterpartyScore"])
            if risk_score < DEFAULT_MIN_RISK:
                self.ledger.append("EXECUTION_BLOCKED", {"opportunityId": req.opportunityId, "reason": "LIVE_RISK_SCORE", "riskScore": risk_score})
                raise HTTPException(status_code=403, detail={"code": "LIVE_GATE_RISK_SCORE", "state": state, "riskScore": risk_score})

        self.ledger.append("SETTLEMENT_AUTHORIZED", {
            "opportunityId": req.opportunityId,
            "authorizationHash": auth_hash,
            "termsHash": terms_hash,
            "contractSignaturesVerified": contract_verified,
            "treasuryDestination": req.treasuryDestination,
        })
        state = self._set_state(req.opportunityId, state, "AUTHORIZED")

        if self.mode == "PAPER":
            receipt = revenue_engine.collect_before_principal_release(accrual)
            self.ledger.append("EXECUTION_ATTEMPTED", {"opportunityId": req.opportunityId, "rail": self.paper_rail.name, "authorizationHash": auth_hash, "simulated": True})
            execution = self.paper_rail.execute(
                amount=evaluated["capital"],
                currency=evaluated["currency"],
                recipient=req.recipient,
                idempotency_key=auth_hash,
                memo="M-FLOW PAPER simulated settlement",
            )
            self.ledger.append("PAPER_SETTLEMENT_SIMULATED", {"opportunityId": req.opportunityId, "transactionHash": execution["transactionHash"], "feeReceipt": receipt, "authorizationHash": auth_hash})
            state = self._set_state(req.opportunityId, state, "SETTLED")
        elif self.mode == "LIVE":
            paypal_env = os.getenv("PAYPAL_ENV", "")
            if paypal_env != "live":
                raise HTTPException(status_code=403, detail={"code": "LIVE_GATE_PAYPAL_ENV", "state": state})
            base_url = "https://api-m.paypal.com"
            rail = PayPalLiveRailAdapter(client_id=os.environ["PAYPAL_CLIENT_ID"], client_secret=os.environ["PAYPAL_CLIENT_SECRET"], base_url=base_url)
            treasury = LiveTreasuryAdapter(rail, req.treasuryDestination)
            live_revenue = RevenueEngine(self.ledger.append, treasury=treasury)
            fee_receipt = live_revenue.collect_before_principal_release(accrual)
            self.ledger.append("EXECUTION_ATTEMPTED", {"opportunityId": req.opportunityId, "rail": rail.name, "authorizationHash": auth_hash, "simulated": False})
            execution = rail.execute(
                amount=evaluated["capital"],
                currency=evaluated["currency"],
                recipient=req.recipient,
                idempotency_key=auth_hash,
                memo="M-FLOW authorized LIVE settlement",
            )
            self.ledger.append("LIVE_SETTLEMENT_SUBMITTED", {"opportunityId": req.opportunityId, "transactionHash": execution["transactionHash"], "feeReceipt": fee_receipt, "authorizationHash": auth_hash})
            state = self._set_state(req.opportunityId, state, "SETTLED")
        else:
            raise HTTPException(status_code=400, detail={"code": "INVALID_MODE", "state": state})

        record = OpportunityRecord(req.opportunityId, state, evaluated["score"], terms_hash, execution)
        self.records[req.opportunityId] = record
        return {
            "ok": True,
            "mode": self.mode,
            "state": state,
            "opportunityId": req.opportunityId,
            "score": evaluated["score"],
            "termsHash": terms_hash,
            "authorizationHash": auth_hash,
            "fees": fee_payload,
            "execution": execution,
            "auditVerified": self.ledger.verify(),
        }


app = FastAPI(title="M-FLOW Commercial Execution API", version="0.2.0")
pipeline = MFlowPipeline()


@app.get("/health")
def health() -> dict[str, Any]:
    readiness = pipeline.readiness.evaluate()

    return {
        "ok": True,
        "service": "M-FLOW",
        "mode": pipeline.mode,
        "auditVerified": pipeline.ledger.verify(),
        "readiness": {
            "ready": readiness["ready"],
            "passedCount": readiness["passedCount"],
            "gateCount": readiness["gateCount"],
        },
    }


@app.get("/api/v1/system/readiness")
def system_readiness() -> dict[str, Any]:
    # Deliberately exposes readiness metadata only; no credential values,
    # access tokens, signatures or private material are returned.
    return {
        "service": "M-FLOW",
        "mode": pipeline.mode,
        **pipeline.readiness.evaluate(),
    }


@app.get("/v1/status")
def status() -> dict[str, Any]:
    return {"mode": pipeline.mode, "opportunities": len(pipeline.records), "auditVerified": pipeline.ledger.verify()}


@app.post("/v1/mode")
def change_mode(payload: dict[str, str]) -> dict[str, str]:
    try:
        pipeline.set_mode(payload["mode"])
    except PermissionError as exc:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "SYSTEM_READINESS_FAILED",
                "message": str(exc),
                "readiness": pipeline.readiness.evaluate(),
            },
        ) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"mode": pipeline.mode}


@app.get("/v1/audit/verify")
def audit_verify() -> dict[str, Any]:
    return {"valid": pipeline.ledger.verify(), "entries": len(pipeline.ledger.entries())}


@app.post("/execute")
@app.post("/evaluate-and-execute")
def execute_trade(request: Request, trade: TradeRequest) -> dict[str, Any]:
    try:
        return pipeline.run(trade)
    except HTTPException:
        raise
    except (ValueError, KeyError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    except Exception as exc:
        # Do not expose exception details or secrets to clients.
        pipeline.ledger.append("EXECUTION_FAILED", {"opportunityId": trade.opportunityId, "errorType": type(exc).__name__})
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR"}) from exc
