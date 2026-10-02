# Security notes

- Secrets belong in environment variables or the deployment secret manager. Never commit `.env`, provider credentials, OAuth tokens, private keys, or raw Authorization headers.
- The server binds to `127.0.0.1` by default. Set `M_FLOW_HOST` explicitly when a controlled deployment needs another interface and place the service behind an authenticated TLS-capable gateway.
- LIVE execution is fail-closed. Missing credentials, unverified evidence, failed margin, missing settlement authorization, wrong provider, wrong rail, or wrong mode blocks execution.
- Evidence signatures are Ed25519-verifiable and each evidence payload has a SHA-256 digest. A signature alone does not establish legal identity; the signer's public-key fingerprint must be pre-trusted for a VERIFIED counterparty.
- Payment execution uses an external provider account. M-FLOW local stores do not implement a pooled customer balance.
- The local JSON ledger is hash-linked and tamper-evident. Production should replicate/anchor it to access-controlled WORM or independently anchored storage.
- The JSON file stores are intentionally dependency-free for this first operational milestone. A multi-instance deployment should use a transactional database plus the same domain interfaces before horizontal scaling.
