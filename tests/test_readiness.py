from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parents[1] / "src"),
)

from engine.registry import CounterpartyRegistry
from engine.readiness import SystemReadinessAuditor


def _signed_payload(payload: dict) -> dict:
    script = r'''
const crypto = require("node:crypto");

const payload = JSON.parse(process.argv[1]);

const {privateKey, publicKey} =
    crypto.generateKeyPairSync("ed25519");

const canonical =
    JSON.stringify(
        Object.fromEntries(
            Object.entries(payload)
                .sort(([a], [b]) => a.localeCompare(b))
        )
    );

const signature =
    crypto.sign(
        null,
        Buffer.from(canonical),
        privateKey
    );

const rawPublic =
    publicKey
        .export({
            type: "spki",
            format: "der"
        })
        .subarray(-32);

process.stdout.write(
    JSON.stringify({
        signatureB64:
            signature.toString("base64"),
        publicKeyB64:
            rawPublic.toString("base64")
    })
);
'''

    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return {
        **payload,
        **json.loads(result.stdout),
    }


def test_registry_requires_document_hash_and_signature(tmp_path):
    registry = CounterpartyRegistry(
        tmp_path / "registry.json"
    )

    with pytest.raises(
        ValueError,
        match="REGISTRY_DOCUMENT_HASH_REQUIRED",
    ):
        registry.register(
            entity_id="B1",
            entity_type="BUYER",
            profile={"legalName": "Buyer"},
            document_hashes=[],
            signature_b64="x",
            public_key_b64="x",
        )


def test_registry_accepts_only_valid_signed_entity(tmp_path):
    registry = CounterpartyRegistry(
        tmp_path / "registry.json"
    )

    document_hash = hashlib.sha256(
        b"real KYC document"
    ).hexdigest()

    payload = {
        "entityId": "B1",
        "entityType": "BUYER",
        "profile": {
            "legalName": "Verified Buyer",
            "verificationStatus": "VERIFIED",
        },
        "documentHashes": [document_hash],
    }

    signed = _signed_payload(payload)

    row = registry.register(
        entity_id=payload["entityId"],
        entity_type="BUYER",
        profile=payload["profile"],
        document_hashes=payload["documentHashes"],
        signature_b64=signed["signatureB64"],
        public_key_b64=signed["publicKeyB64"],
    )

    assert row["verified"] is True

    assert registry.document_hash_registered(
        document_hash,
        "B1",
    )


def test_readiness_has_exactly_ten_fail_closed_gates(tmp_path):
    registry = CounterpartyRegistry(
        tmp_path / "registry.json"
    )

    auditor = SystemReadinessAuditor(
        registry,
        tmp_path / "readiness.json",
    )

    report = auditor.evaluate()

    assert report["ready"] is False
    assert report["gateCount"] == 10
    assert report["passedCount"] == 0
    assert len(report["failedGates"]) == 10
    assert len(report["gates"]) == 10


def test_readiness_never_returns_paypal_secret(tmp_path, monkeypatch):
    registry = CounterpartyRegistry(
        tmp_path / "registry.json"
    )

    auditor = SystemReadinessAuditor(
        registry,
        tmp_path / "readiness.json",
    )

    monkeypatch.setenv(
        "PAYPAL_CLIENT_ID",
        "test-client-id",
    )

    monkeypatch.setenv(
        "PAYPAL_CLIENT_SECRET",
        "SUPER-SECRET-MUST-NOT-LEAK",
    )

    monkeypatch.setenv(
        "PAYPAL_ENV",
        "live",
    )

    rendered = json.dumps(
        auditor.evaluate()
    )

    assert "SUPER-SECRET-MUST-NOT-LEAK" not in rendered
    assert "credentialsPresent" in rendered


def test_mode_transition_is_blocked_when_readiness_fails():
    from api.main import MFlowPipeline

    pipeline = MFlowPipeline(
        mode="DISCOVERY"
    )

    with pytest.raises(
        PermissionError,
        match="READINESS_GATES_FAILED",
    ):
        pipeline.set_mode("PAPER")

    assert pipeline.mode == "DISCOVERY"
