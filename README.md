# M-FLOW

M-FLOW is a proprietary capital-and-trade intelligence engine. It is designed to discover, verify, score, document, authorize, and execute transactions through external authorized capital/payment providers without taking custody of third-party funds.

## Operating modes

`DISCOVERY -> PAPER -> LIVE` is enforced by a fail-closed state machine. LIVE execution requires:

1. real provider credentials in the process environment,
2. cryptographically verified counterparty evidence,
3. a settlement-authorization hash recorded in the append-only audit ledger.

An emergency demotion to DISCOVERY is allowed to fail closed.

## Financial integrity

Financial values use a fixed six-decimal BigInt representation. JavaScript floating-point arithmetic is not used for money, margins, financing costs, or capital velocity.

## Audit integrity

Every important state change, score calculation, document generation, and execution attempt is appended as a SHA-256 hash-linked JSONL entry. The validator stops at the first broken chain link.

The file ledger is tamper-evident, not a claim of physical immutability. Production deployments should additionally replicate/anchor the ledger to controlled WORM or independently anchored storage.

## Payment rails

All settlement rails implement `PaymentRailAdapter`. PayPal is one adapter and is not required by the core engine. Live PayPal API calls use OAuth 2.0 and the official REST API.

## Safety / regulatory boundary

M-FLOW contains no fabricated lenders, transactions, approvals, KYC results, invoices, credentials, or profitability claims. Counterparty and provider records must be backed by real evidence before they can pass LIVE gates. Cryptographic signatures establish integrity/authenticity of a supplied key; production trust additionally requires the signer key fingerprint to be pre-trusted and the identity/risk evidence to be independently authoritative.

The engine does not itself constitute a Kenyan lender, bank, payment service provider, investment platform, or legal authorization. Real deployment must use appropriately licensed/authorized providers and rails where required.

## Run

```powershell
npm install
Copy-Item .env.example .env
npm run verify
npm run dev
```

API default: `http://localhost:8787`

Health: `GET /health`
Mode: `GET /v1/mode`
Opportunities: `POST /v1/opportunities`
Counterparties: `POST /v1/counterparties`
Provider registry: `POST /v1/providers`
Provider matching: `POST /v1/provider-matches`
Gate status: `GET /v1/status/gates`
Opportunity state changes: `POST /v1/opportunities/:id/state`
Settlement authorization: `POST /v1/settlement-authorizations`
Execution: `POST /v1/executions`
Audit: `GET /v1/audit/verify`

For live operation, configure real credentials and real evidence outside source control. The included provider registry starts empty by design.

## Example opportunity shape

All monetary/financial values are strings with up to six decimal places. Example values below are test input, not market data or a funding commitment.

```json
{
  "volume": "100",
  "unitPrice": "125.50",
  "leadTimeDays": "3",
  "counterpartyId": "<REAL_COUNTERPARTY_ID>",
  "marginThreshold": "0.10",
  "cogs": "9000.00",
  "financingCost": "500.00",
  "platformFee": "25.00",
  "requiredCapital": "9500.00",
  "durationDays": "5",
  "counterpartyScore": 9000,
  "volatilityFactor": 1000
}
```
