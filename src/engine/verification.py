from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import base64
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_ed25519_spki(raw_public_key: bytes) -> bytes:
    # RFC 8410 SubjectPublicKeyInfo prefix for Ed25519 + 32-byte raw public key.
    return bytes.fromhex("302a300506032b6570032100") + raw_public_key


def verify_ed25519_signature(payload: Any, signature_b64: str, public_key_b64: str) -> bool:
    """Verify an Ed25519 signature using Node's audited built-in crypto implementation.

    M-FLOW already requires Node for its production runtime, so this avoids introducing a
    second cryptography implementation or an unverified third-party crypto dependency.
    """
    message = base64.b64encode(canonical_json(payload)).decode("ascii")
    signature = base64.b64decode(signature_b64, validate=True)
    raw_public_key = base64.b64decode(public_key_b64, validate=True)
    if len(raw_public_key) != 32 or len(signature) != 64:
        raise ValueError("ED25519_KEY_OR_SIGNATURE_LENGTH_INVALID")
    spki_b64 = base64.b64encode(_raw_ed25519_spki(raw_public_key)).decode("ascii")
    script = r'''
const crypto = require('node:crypto');
const [messageB64, signatureB64, spkiB64] = process.argv.slice(1);
const ok = crypto.verify(null, Buffer.from(messageB64, 'base64'),
  crypto.createPublicKey({key: Buffer.from(spkiB64, 'base64'), format: 'der', type: 'spki'}),
  Buffer.from(signatureB64, 'base64'));
process.stdout.write(ok ? '1' : '0');
'''
    result = subprocess.run(
        ["node", "-e", script, message, base64.b64encode(signature).decode("ascii"), spki_b64],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("CRYPTO_BRIDGE_FAILED")
    if result.stdout.strip() != "1":
        raise ValueError("INVALID_ED25519_SIGNATURE")
    return True


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_type: str
    payload: dict[str, Any]
    content_sha256: str
    signature_b64: str
    public_key_b64: str

    def verify(self, expected_file: str | Path | None = None) -> dict[str, Any]:
        if expected_file is not None and sha256_file(expected_file) != self.content_sha256.lower():
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        verify_ed25519_signature(self.payload, self.signature_b64, self.public_key_b64)
        return {
            "verified": True,
            "evidenceType": self.evidence_type.upper(),
            "contentSha256": self.content_sha256.lower(),
            "payloadSha256": hashlib.sha256(canonical_json(self.payload)).hexdigest(),
        }


def validate_invoice_metadata(invoice: dict[str, Any], vendor: dict[str, Any]) -> dict[str, Any]:
    required_invoice = ["invoiceNumber", "vendorId", "amount", "currency", "issueDate", "dueDate"]
    for field in required_invoice:
        if field not in invoice or invoice[field] in (None, ""):
            raise ValueError(f"INVOICE_FIELD_REQUIRED:{field}")
    if invoice["vendorId"] != vendor["vendorId"]:
        raise ValueError("INVOICE_VENDOR_MISMATCH")
    if str(invoice["currency"]).upper() != str(vendor["currency"]).upper():
        raise ValueError("INVOICE_CURRENCY_MISMATCH")
    amount = str(invoice["amount"])
    if not amount.replace(".", "", 1).isdigit() or amount.count(".") > 1:
        raise ValueError("INVOICE_AMOUNT_INVALID")
    issue = date.fromisoformat(invoice["issueDate"])
    due = date.fromisoformat(invoice["dueDate"])
    if due < issue:
        raise ValueError("INVOICE_DUE_DATE_INVALID")
    if "legalName" in vendor and invoice.get("vendorLegalName") != vendor["legalName"]:
        raise ValueError("INVOICE_LEGAL_NAME_MISMATCH")
    return {"verified": True, "invoiceNumber": invoice["invoiceNumber"], "vendorId": invoice["vendorId"]}


def verify_notice_of_assignment(notice: dict[str, Any], assignment_hash: str, expected_settlement_ref: str) -> dict[str, Any]:
    if notice.get("acknowledged") is not True:
        raise ValueError("ASSIGNMENT_NOT_ACKNOWLEDGED")
    if notice.get("assignmentHash") != assignment_hash:
        raise ValueError("ASSIGNMENT_HASH_MISMATCH")
    if notice.get("settlementReference") != expected_settlement_ref:
        raise ValueError("SETTLEMENT_REFERENCE_MISMATCH")
    return {"verified": True, "acknowledgedBy": notice.get("acknowledgedBy")}


def counterparty_risk_score(base_score: int, settlement_velocity: float, invoice_age_days: int, verified_debt_ratio: float) -> int:
    if not 0 <= base_score <= 10000:
        raise ValueError("BASE_SCORE_RANGE")
    if not 0 <= settlement_velocity <= 1:
        raise ValueError("VELOCITY_RANGE")
    if invoice_age_days < 0:
        raise ValueError("INVOICE_AGE_INVALID")
    if not 0 <= verified_debt_ratio <= 1:
        raise ValueError("DEBT_RATIO_RANGE")
    age_penalty = min(invoice_age_days / 180.0, 1.0)
    adjusted = base_score + (settlement_velocity * 1000) - (age_penalty * 1500) - (verified_debt_ratio * 2000)
    return max(0, min(10000, round(adjusted)))


def verify_three_party_contract_signatures(terms_hash: str, signatures: list[dict[str, str]]) -> dict[str, Any]:
    required = {"BUYER", "SUPPLIER", "CAPITAL_PROVIDER"}
    seen: set[str] = set()
    for item in signatures:
        role = item.get("role", "").upper()
        if role in seen or role not in required:
            raise ValueError("CONTRACT_SIGNATURE_ROLE_INVALID")
        payload = {"role": role, "termsHash": terms_hash}
        verify_ed25519_signature(payload, item["signatureB64"], item["publicKeyB64"])
        seen.add(role)
    if seen != required:
        raise ValueError("CONTRACT_SIGNATURES_INCOMPLETE")
    return {"verified": True, "roles": sorted(seen)}


@dataclass(frozen=True)
class LiveGateResult:
    allowed: bool
    reasons: tuple[str, ...]


def live_settlement_gate(*, credentials_ready: bool, evidence_verified: bool, contract_signatures_verified: bool,
                         ledger_authorized: bool, risk_score: int, minimum_risk_score: int = 7000) -> LiveGateResult:
    reasons: list[str] = []
    if not credentials_ready:
        reasons.append("LIVE_GATE_PROVIDER_CREDENTIALS")
    if not evidence_verified:
        reasons.append("LIVE_GATE_EVIDENCE")
    if not contract_signatures_verified:
        reasons.append("LIVE_GATE_CONTRACT_SIGNATURES")
    if not ledger_authorized:
        reasons.append("LIVE_GATE_LEDGER_AUTHORIZATION")
    if not (0 <= risk_score <= 10000) or risk_score < minimum_risk_score:
        reasons.append("LIVE_GATE_RISK_SCORE")
    return LiveGateResult(allowed=not reasons, reasons=tuple(reasons))
