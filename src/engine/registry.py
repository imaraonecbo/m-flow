from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from engine.verification import canonical_json, verify_ed25519_signature

EntityType = Literal["BUYER", "SUPPLIER", "CAPITAL_PROVIDER"]
ENTITY_TYPES = {"BUYER", "SUPPLIER", "CAPITAL_PROVIDER"}


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _valid_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


def _load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    raw = path.read_text(encoding="utf-8").strip()

    if not raw:
        return []

    data = json.loads(raw)

    if not isinstance(data, list):
        raise ValueError("REGISTRY_FILE_INVALID")

    return data


class CounterpartyRegistry:
    """
    Persistent registry for real M-FLOW counterparties.

    No entity becomes VERIFIED merely because a boolean was supplied.
    Registration requires:

      1. valid entity type
      2. non-empty profile
      3. at least one SHA-256 document hash
      4. valid Ed25519 signature over the canonical registration payload

    Document contents and private credentials are never stored here.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path
            or os.getenv(
                "M_FLOW_REGISTRY_FILE",
                "data/counterparty_registry.json",
            )
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def canonical_registration_payload(
        *,
        entity_id: str,
        entity_type: EntityType,
        profile: dict[str, Any],
        document_hashes: list[str],
    ) -> dict[str, Any]:
        return {
            "entityId": entity_id,
            "entityType": entity_type,
            "profile": profile,
            "documentHashes": sorted(set(document_hashes)),
        }

    @staticmethod
    def _validate_registration(record: dict[str, Any]) -> None:
        entity_id = record.get("entityId")
        entity_type = str(record.get("entityType", "")).upper()
        profile = record.get("profile")
        hashes = record.get("documentHashes")
        signature = record.get("signatureB64")
        public_key = record.get("publicKeyB64")

        if (
            not isinstance(entity_id, str)
            or not entity_id.strip()
            or len(entity_id) > 256
        ):
            raise ValueError("REGISTRY_ENTITY_ID_INVALID")

        if entity_type not in ENTITY_TYPES:
            raise ValueError("REGISTRY_ENTITY_TYPE_INVALID")

        if not isinstance(profile, dict) or not profile:
            raise ValueError("REGISTRY_PROFILE_REQUIRED")

        if (
            not isinstance(hashes, list)
            or not hashes
            or any(not _valid_sha(h) for h in hashes)
        ):
            raise ValueError("REGISTRY_DOCUMENT_HASH_REQUIRED")

        if not isinstance(signature, str) or not isinstance(public_key, str):
            raise ValueError("REGISTRY_SIGNATURE_REQUIRED")

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp = tempfile.mkstemp(
            prefix=".registry-",
            suffix=".tmp",
            dir=str(self.path.parent),
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    records,
                    fh,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())

            os.replace(tmp, self.path)

        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(_load_json(self.path)))

    def get(self, entity_id: str) -> dict[str, Any] | None:
        with self._lock:
            for row in _load_json(self.path):
                if row.get("entityId") == entity_id:
                    return json.loads(json.dumps(row))

        return None

    def register(
        self,
        *,
        entity_id: str,
        entity_type: EntityType,
        profile: dict[str, Any],
        document_hashes: list[str],
        signature_b64: str,
        public_key_b64: str,
        actor: str = "operator",
    ) -> dict[str, Any]:

        entity_type = str(entity_type).upper()

        record = {
            "entityId": entity_id,
            "entityType": entity_type,
            "profile": profile,
            "documentHashes": sorted(set(document_hashes)),
            "signatureB64": signature_b64,
            "publicKeyB64": public_key_b64,
            "registeredBy": actor,
        }

        self._validate_registration(record)

        payload = self.canonical_registration_payload(
            entity_id=entity_id,
            entity_type=entity_type,
            profile=profile,
            document_hashes=document_hashes,
        )

        verify_ed25519_signature(
            payload,
            signature_b64,
            public_key_b64,
        )

        record["registrationHash"] = _sha256(payload)
        record["verified"] = True

        with self._lock:
            rows = _load_json(self.path)

            if any(row.get("entityId") == entity_id for row in rows):
                raise ValueError("REGISTRY_ENTITY_ALREADY_EXISTS")

            rows.append(record)
            self._write(rows)

        # Never return public-key/signature material unnecessarily.
        return {
            k: v
            for k, v in record.items()
            if k not in {"signatureB64", "publicKeyB64"}
        }

    def verified(
        self,
        entity_type: EntityType | None = None,
    ) -> list[dict[str, Any]]:

        wanted = str(entity_type).upper() if entity_type else None

        return [
            row
            for row in self.all()
            if row.get("verified") is True
            and (wanted is None or row.get("entityType") == wanted)
        ]

    def document_hash_registered(
        self,
        content_sha256: str,
        entity_id: str | None = None,
    ) -> bool:

        if not _valid_sha(content_sha256):
            return False

        target = content_sha256.lower()

        for row in self.verified():
            if entity_id and row.get("entityId") != entity_id:
                continue

            hashes = {
                str(x).lower()
                for x in row.get("documentHashes", [])
            }

            if target in hashes:
                return True

        return False
