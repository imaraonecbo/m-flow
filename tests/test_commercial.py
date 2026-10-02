from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from engine.documents import TradeTerms, generate_trade_documents
from engine.revenue import RevenueEngine, TreasuryAdapter
from engine.verification import (
    live_settlement_gate,
    verify_ed25519_signature,
    verify_three_party_contract_signatures,
    validate_invoice_metadata,
)


def node_signature_fixture(payload: dict):
    script = r"""
const crypto = require('node:crypto');
const payload = JSON.parse(process.argv[1]);
const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
const canonical = JSON.stringify(Object.fromEntries(Object.entries(payload).sort(([a],[b]) => a.localeCompare(b))));
const signature = crypto.sign(null, Buffer.from(canonical), privateKey);
const rawPublic = publicKey.export({ type: 'spki', format: 'der' }).subarray(-32);
process.stdout.write(JSON.stringify({
  publicKeyB64: rawPublic.toString('base64'),
  signatureB64: signature.toString('base64')
}));
"""
    result = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class TestCommercialControls(unittest.TestCase):
    def test_all_documents_bind_to_same_terms_hash(self):
        terms = TradeTerms("OP-1", "1000000.00", "120000.00", "2026-10-08", "B-1", "S-1", "CP-1", "KES", "800000", "25000", "15000")
        docs = generate_trade_documents(terms)
        self.assertEqual(len(docs), 3)
        for item in docs.values():
            self.assertEqual(item["termsHash"], terms.terms_hash)
            self.assertIn(f"Trade Terms SHA-256: {terms.terms_hash}", item["markdown"])
            self.assertTrue(item["pdf"].startswith(b"%PDF-1.4"))

    def test_ed25519_signature_verifies(self):
        payload = {"termsHash": "abc123", "role": "BUYER"}
        fixture = node_signature_fixture(payload)
        self.assertTrue(verify_ed25519_signature(payload, fixture["signatureB64"], fixture["publicKeyB64"]))
        with self.assertRaises(ValueError):
            verify_ed25519_signature({"termsHash": "tampered", "role": "BUYER"}, fixture["signatureB64"], fixture["publicKeyB64"])

    def test_live_gate_is_fail_closed_until_every_requirement_is_true(self):
        blocked = live_settlement_gate(credentials_ready=True, evidence_verified=True,
                                       contract_signatures_verified=False, ledger_authorized=True,
                                       risk_score=9000)
        self.assertFalse(blocked.allowed)
        self.assertIn("LIVE_GATE_CONTRACT_SIGNATURES", blocked.reasons)
        allowed = live_settlement_gate(credentials_ready=True, evidence_verified=True,
                                        contract_signatures_verified=True, ledger_authorized=True,
                                        risk_score=9000)
        self.assertTrue(allowed.allowed)

    def test_platform_fee_exact_fixed_precision(self):
        events = []
        engine = RevenueEngine(lambda event, payload: events.append((event, payload)))
        accrual = engine.calculate(opportunity_id="OP-2", deal_volume="1000000.00", net_yield="100000.00",
                                   origination_bps=150, spread_bps=200, currency="KES", authorization_hash="a" * 64)
        self.assertEqual(str(accrual.origination_fee), "15000.00")
        self.assertEqual(str(accrual.spread_income), "2000.00")
        self.assertEqual(str(accrual.total_platform_revenue), "17000.00")
        self.assertEqual(events[0][0], "PLATFORM_FEE_ACCRUED")

    def test_principal_release_requires_treasury_confirmation(self):
        events = []
        engine = RevenueEngine(lambda event, payload: events.append((event, payload)))
        accrual = engine.calculate(opportunity_id="OP-3", deal_volume="500000", net_yield="50000",
                                   origination_bps=150, spread_bps=0, authorization_hash="b" * 64)
        with self.assertRaisesRegex(RuntimeError, "TREASURY_ADAPTER_REQUIRED"):
            engine.collect_before_principal_release(accrual)

        class TestTreasury(TreasuryAdapter):
            name = "test-only"
            def collect_platform_fee(self, *, opportunity_id, amount, currency, authorization_hash):
                return "TEST-RECEIPT"

        engine = RevenueEngine(lambda event, payload: events.append((event, payload)), treasury=TestTreasury())
        self.assertEqual(engine.collect_before_principal_release(accrual), "TEST-RECEIPT")
        self.assertEqual([e[0] for e in events[-2:]], ["PLATFORM_FEE_SETTLED", "PRINCIPAL_RELEASE_AUTHORIZED"])


    def test_three_party_contract_signatures_require_all_roles(self):
        terms_hash = "f" * 64
        signatures = []
        for role in ("BUYER", "SUPPLIER", "CAPITAL_PROVIDER"):
            fixture = node_signature_fixture({"role": role, "termsHash": terms_hash})
            signatures.append({"role": role, "signatureB64": fixture["signatureB64"], "publicKeyB64": fixture["publicKeyB64"]})
        result = verify_three_party_contract_signatures(terms_hash, signatures)
        self.assertTrue(result["verified"])
        with self.assertRaisesRegex(ValueError, "CONTRACT_SIGNATURES_INCOMPLETE"):
            verify_three_party_contract_signatures(terms_hash, signatures[:2])

    def test_invoice_metadata_must_match_vendor_record(self):
        invoice = {
            "invoiceNumber": "INV-001", "vendorId": "V-1", "vendorLegalName": "Vendor One",
            "amount": "125000.00", "currency": "KES", "issueDate": "2026-10-01", "dueDate": "2026-10-31"
        }
        vendor = {"vendorId": "V-1", "legalName": "Vendor One", "currency": "KES"}
        self.assertTrue(validate_invoice_metadata(invoice, vendor)["verified"])
        invoice["vendorLegalName"] = "Other Vendor"
        with self.assertRaisesRegex(ValueError, "INVOICE_LEGAL_NAME_MISMATCH"):
            validate_invoice_metadata(invoice, vendor)
    def test_signature_and_document_hash_are_real_sha256_bound_values(self):
        terms = TradeTerms("OP-9", "2500.00", "400.00", "2026-10-09", "B", "S", "CP", "KES", "1800", "50", "37.50")
        expected = __import__("hashlib").sha256(
            json.dumps(terms.normalized(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.assertEqual(terms.terms_hash, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
