from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from api.main import MFlowPipeline, app


def signed_evidence(content: bytes, evidence_type: str, role: str):
    import subprocess

    payload = {"role": role, "evidenceType": evidence_type, "contentSha256": hashlib.sha256(content).hexdigest()}
    script = r'''
const crypto = require('node:crypto');
const payload = JSON.parse(process.argv[1]);
const {privateKey, publicKey} = crypto.generateKeyPairSync('ed25519');
const canonical = JSON.stringify(Object.fromEntries(Object.entries(payload).sort(([a],[b]) => a.localeCompare(b))));
const signature = crypto.sign(null, Buffer.from(canonical), privateKey);
const rawPublic = publicKey.export({type:'spki',format:'der'}).subarray(-32);
process.stdout.write(JSON.stringify({signatureB64: signature.toString('base64'), publicKeyB64: rawPublic.toString('base64')}));
'''
    import subprocess
    out = subprocess.run(["node", "-e", script, json.dumps(payload)], capture_output=True, text=True, check=True)
    sig = json.loads(out.stdout)
    return {
        "type": evidence_type,
        "contentBase64": base64.b64encode(content).decode(),
        "contentSha256": payload["contentSha256"],
        "payload": payload,
        **sig,
    }


def base_payload(mode="PAPER"):
    identity_content = b"verified identity evidence"
    invoice_content = b"invoice evidence INV-100"
    identity = signed_evidence(identity_content, "KYC", "COUNTERPARTY")
    invoice = signed_evidence(invoice_content, "INVOICE", "COUNTERPARTY")
    return {
        "opportunityId": "OP-HTTP-1",
        "capital": "100000.00",
        "revenue": "120000.00",
        "cogs": "95000.00",
        "financingCost": "1000.00",
        "durationDays": 10,
        "marginThreshold": "0.10",
        "currency": "KES",
        "settlementDate": "2026-10-10",
        "buyerId": "B-1",
        "supplierId": "S-1",
        "capitalProviderId": "CP-1",
        "counterparty": {
            "identityStatus": "VERIFIED",
            "creditRiskScore": 9000,
            "historicalSettlementVelocity": "0.8",
            "invoiceAgeDays": 5,
            "verifiedDebtRatio": "0.1",
            "evidence": [
                {"contentSha256": identity["contentSha256"]},
                {"contentSha256": invoice["contentSha256"]},
            ],
        },
        "evidenceRecords": [identity, invoice],
        "contractSignatures": [],
        "treasuryDestination": "PAPER-TREASURY-TEST",
        "recipient": "paper-recipient@example.test",
        "rail": "paper",
        "volatilityFactor": 500,
        "originationBps": 200,
        "spreadBps": 0,
    }


def test_fee_accrual_and_state_transition_are_one_http_request():
    import api.main as main
    main.pipeline = MFlowPipeline(mode="PAPER")
    client = TestClient(main.app)
    response = client.post("/evaluate-and-execute", json=base_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "SETTLED"
    assert data["fees"]["originationFee"] == "2000.00"
    assert data["auditVerified"] is True
    events = [entry["eventType"] for entry in main.pipeline.ledger.entries()]
    assert "PLATFORM_FEE_ACCRUED" in events
    assert "PLATFORM_FEE_SETTLED" in events
    assert "PRINCIPAL_RELEASE_AUTHORIZED" in events
    assert "PAPER_SETTLEMENT_SIMULATED" in events
    assert events.index("PLATFORM_FEE_ACCRUED") < events.index("PAPER_SETTLEMENT_SIMULATED")


def test_discovery_blocks_before_settlement():
    import api.main as main
    main.pipeline = MFlowPipeline(mode="DISCOVERY")
    client = TestClient(main.app)
    response = client.post("/execute", json=base_payload(mode="DISCOVERY"))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DISCOVERY_EXECUTION_BLOCKED"
    assert all(e["eventType"] != "PAPER_SETTLEMENT_SIMULATED" for e in main.pipeline.ledger.entries())


def test_live_blocks_without_credentials_and_contract_signatures():
    import api.main as main
    main.pipeline = MFlowPipeline(mode="LIVE")
    os.environ["PAYPAL_CLIENT_ID"] = "TEST_ONLY_NOT_A_REAL_CREDENTIAL"
    os.environ["PAYPAL_CLIENT_SECRET"] = "TEST_ONLY_NOT_A_REAL_SECRET"
    os.environ["PAYPAL_ENV"] = "live"
    os.environ["M_FLOW_LIVE_EXECUTION_ENABLED"] = "true"
    client = TestClient(main.app)
    response = client.post("/execute", json=base_payload())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "LIVE_GATE_CONTRACT_SIGNATURES"
