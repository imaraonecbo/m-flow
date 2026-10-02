# M-FLOW Commercial Python Controls

This sidecar implements the requested commercial controls without introducing a second cryptocurrency or payment implementation:

- `src/engine/documents.py`: Tri-Party Trade Financing Agreement, Irrevocable Assignment of Receivables, Platform Services & Origination Fee Agreement, SHA-256 trade-term binding, and dependency-free PDF output.
- `src/engine/verification.py`: evidence hashing, Ed25519 verification through Node's built-in crypto, invoice metadata checks, notice-of-assignment checks, counterparty risk modifier, and fail-closed LIVE gate.
- `src/engine/revenue.py`: fixed-precision origination/spread revenue, immutable-ledger event callbacks, and fee-confirmed-before-principal-release sequencing.
- `tests/test_commercial.py`: unit tests for document binding, real Ed25519 verification, LIVE fail-closed behavior, exact fees, treasury confirmation ordering, and SHA-256 binding.

The production runtime remains Node.js. These Python modules are an auditable commercial-control sidecar and do not replace the live Node engine.

Legal note: generated documents are transaction templates. Whether they are enforceable depends on applicable law, execution formalities, counterparty authority, and qualified legal review.
