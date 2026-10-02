# M-FLOW production gate checklist

The codebase starts in DISCOVERY and contains no real provider credentials or provider commitments. LIVE execution is intentionally impossible until external prerequisites exist.

## Required before LIVE

- A real, authorized capital/payment provider is registered with `verified: true`, correct supported currencies and rails, and evidence URLs/verification records.
- Real provider API credentials are supplied through process environment variables only. They are never stored in the repository.
- Each executable counterparty is marked `identityStatus: VERIFIED`, has a verified `creditRiskScore` from an appropriate authoritative source, and has cryptographically verified evidence.
- The evidence signing public-key fingerprints are explicitly trusted for that counterparty.
- Verified evidence includes identity/risk material and transaction material such as an invoice, contract, or purchase order.
- The transaction passes its configured margin threshold.
- A settlement authorization is created and its hash is recorded in the audit ledger.
- LIVE mode is entered successfully; otherwise the engine remains fail-closed.

## External compliance boundary

This engine does not by itself grant a financial, lending, investment, or payment authorization. Real deployment in Kenya must use the appropriate licensed/authorized institutions and rails for the activities actually performed.

The engine is non-custodial by design: it does not create a pooled customer balance or hold third-party capital in its local stores. Payment execution uses configured external provider accounts.

## Provider discovery

The engine deliberately distinguishes provider discovery from provider authorization. The registry/matching API ranks only operator-supplied provider records marked verified. It does not scrape or invent funding offers. Any future automated discovery connector must retain source evidence, terms, timestamp, and verification status before a provider can become executable.
