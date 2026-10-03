import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { URL } from 'node:url';
import crypto from 'node:crypto';
import { JsonStore } from './data/store.js';
import { AuditLedger } from './ledger/ledger.js';
import { ModeManager } from './mode/state.js';
import { validateOpportunity } from './opportunities/schema.js';
import { scoreOpportunity } from './risk/scoring.js';
import { verifyEvidence } from './crypto/evidence.js';
import { PayPalAdapter } from './payments/paypal.js';
import { buildTermsSheet, buildCapitalDistributionContract } from './documents/documents.js';
import { matchProviders } from './providers/matching.js';
import { canonicalize } from './core/canonical.js';
import { FixedDecimal } from './finance/decimal.js';
import { MFlowError } from './core/errors.js';
import { log } from './core/logging.js';
import { AcquisitionEngine } from './acquisition/engine.js';

const dataDir = path.resolve(process.env.M_FLOW_DATA_DIR ?? './data');
fs.mkdirSync(dataDir, { recursive: true });
const ledger = new AuditLedger(path.resolve(process.env.M_FLOW_LEDGER_FILE ?? path.join(dataDir, 'ledger.jsonl')));
const opportunities = new JsonStore(path.join(dataDir, 'opportunities.json'));
const counterparties = new JsonStore(path.join(dataDir, 'counterparties.json'));
const documents = new JsonStore(path.join(dataDir, 'documents.json'));
const executions = new JsonStore(path.join(dataDir, 'executions.json'));
const providers = new JsonStore(path.resolve(process.env.M_FLOW_PROVIDER_CONFIG ?? path.join(dataDir, 'providers.json')));
const acquisition = new AcquisitionEngine({ ledger, providerStore: providers });

function gateStatus() {
  return { providerCredentials: providerCredentialsReady(), counterpartyEvidence: verifiedCounterpartyExists(), settlementAuthorization: settlementAuthorizationExists() };
}
function providerCredentialsReady() {
  return Boolean(process.env.PAYPAL_CLIENT_ID && process.env.PAYPAL_CLIENT_SECRET && process.env.PAYPAL_ENV === 'live');
}
function verifiedCounterpartyExists() {
  return counterparties.all().some((c) => c.identityStatus === 'VERIFIED' && c.evidence?.some((e) => e.verified === true));
}
function settlementAuthorizationExists() {
  return ledger.hasEvent('SETTLEMENT_AUTHORIZED', (p) => Boolean(p.authorizationHash));
}
const modeManager = new ModeManager(path.join(dataDir, 'mode.json'), ledger, () => ({
  providerCredentials: providerCredentialsReady(),
  counterpartyEvidence: verifiedCounterpartyExists(),
  settlementAuthorization: settlementAuthorizationExists()
}));
const paypal = new PayPalAdapter();
const OPPORTUNITY_STATES = ['DISCOVERED', 'PAPER_READY', 'FUNDING_MATCHED', 'AUTHORIZED', 'EXECUTING', 'SETTLED', 'FAILED'];

function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(body));
}
async function body(req) {
  let raw = ''; for await (const chunk of req) raw += chunk;
  if (!raw) return {};
  if (Buffer.byteLength(raw, 'utf8') > 1_000_000) throw new MFlowError('BODY_TOO_LARGE', 'Request body exceeds 1MB', 413);
  try { return JSON.parse(raw); } catch { throw new MFlowError('INVALID_JSON', 'Request body is not valid JSON'); }
}
function validateSettlementInput(b) {
  if (typeof b.amount !== 'string' || !/^\d+(?:\.\d{1,6})?$/.test(b.amount) || b.amount === '0') throw new MFlowError('INVALID_SETTLEMENT', 'amount must be a positive fixed-precision decimal string');
  if (typeof b.currency !== 'string' || !/^[A-Z]{3}$/.test(b.currency)) throw new MFlowError('INVALID_SETTLEMENT', 'currency must be a 3-letter uppercase code');
}

function liveExecutionGate(opportunity, counterparty, authorizationHash, settlement) {
  if (modeManager.get() !== 'LIVE') throw new MFlowError('LIVE_ONLY', 'Real execution requires LIVE mode', 423);
  if (!providerCredentialsReady()) throw new MFlowError('LIVE_GATE_PROVIDER', 'Real provider credentials are missing', 423);
  if (counterparty?.identityStatus !== 'VERIFIED') throw new MFlowError('LIVE_GATE_IDENTITY', 'Counterparty identity is not marked VERIFIED', 423);
  if (!Number.isInteger(counterparty.creditRiskScore) || counterparty.creditRiskScore < 0 || counterparty.creditRiskScore > 10000) throw new MFlowError('LIVE_GATE_RISK_SCORE', 'Counterparty creditRiskScore must be verified and be an integer from 0 to 10000', 423);
  const verified = counterparty?.evidence?.filter((e) => e.verified === true) ?? [];
  if (!verified.length) throw new MFlowError('LIVE_GATE_EVIDENCE', 'Counterparty has no cryptographically verified evidence', 423);
  if (!verified.some((e) => ['KYC','AML','IDENTITY'].includes(String(e.type).toUpperCase())) || !verified.some((e) => ['INVOICE','CONTRACT','PURCHASE_ORDER'].includes(String(e.type).toUpperCase()))) throw new MFlowError('LIVE_GATE_EVIDENCE_SET', 'LIVE execution requires verified identity/risk evidence plus verified transaction evidence', 423);
  if (opportunity.score?.marginPass !== true) throw new MFlowError('LIVE_GATE_MARGIN', 'Opportunity has not passed its configured margin threshold', 423);
  const authEvent = ledger.entries().find((e) => e.eventType === 'SETTLEMENT_AUTHORIZED' && e.payload.authorizationHash === authorizationHash && e.payload.opportunityId === opportunity.id);
  if (!authEvent) throw new MFlowError('LIVE_GATE_AUTHORIZATION', 'Settlement authorization hash is absent or does not match the opportunity', 423);
}

async function route(req, res) {
  const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`);
  const method = req.method ?? 'GET';
  if (method === 'GET' && url.pathname === '/health') return send(res, 200, { ok: true, service: 'M-FLOW', mode: modeManager.get(), audit: ledger.verify() });
  if (method === 'GET' && url.pathname === '/v1/mode') return send(res, 200, { mode: modeManager.get() });
  if (method === 'GET' && url.pathname === '/v1/status/gates') return send(res, 200, { mode: modeManager.get(), gates: gateStatus() });
  if (method === 'GET' && url.pathname === '/v1/providers') return send(res, 200, { providers: providers.all().map(({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active }) => ({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active })) });
  if (method === 'GET' && url.pathname === '/v1/acquisition/status') return send(res, 200, { acquisition: acquisition.sourceStatus(), automationEnabled: process.env.M_FLOW_AUTODISCOVERY_ENABLED === 'true' });
  if (method === 'GET' && url.pathname === '/v1/acquisition/opportunities') return send(res, 200, { opportunities: acquisition.opportunities.all() });
  if (method === 'POST' && url.pathname === '/v1/acquisition/discover') { const b = await body(req); const result = await acquisition.discover(b); return send(res, 200, result); }
  if (method === 'POST' && url.pathname === '/v1/acquisition/suppliers/discover') { const b = await body(req); const result = await acquisition.supplierCandidates(b); return send(res, 200, { candidates: result }); }
  if (method === 'POST' && url.pathname === '/v1/acquisition/capital/match') { const b = await body(req); if (typeof b.amount !== 'string' || typeof b.currency !== 'string') throw new MFlowError('INVALID_CAPITAL_MATCH','amount and currency are required'); await acquisition.refreshCapitalFeed(); return send(res, 200, { matches: acquisition.capitalMatches(b) }); }
  if (method === 'GET' && url.pathname === '/v1/acquisition/capital/feed') return send(res, 200, await acquisition.refreshCapitalFeed());
  if (method === 'POST' && url.pathname === '/v1/acquisition/invoice/verify') { const b = await body(req); return send(res, 200, await acquisition.verifyInvoice(b)); }
  if (method === 'POST' && url.pathname === '/v1/acquisition/kyb/company') { const b = await body(req); return send(res, 200, await acquisition.kyb.createCompany(b)); }
  if (method === 'GET' && url.pathname.startsWith('/v1/acquisition/kyb/status/')) { const id = decodeURIComponent(url.pathname.slice('/v1/acquisition/kyb/status/'.length)); return send(res, 200, await acquisition.kyb.statusByExternalUserId(id)); }
  if (method === 'POST' && url.pathname === '/v1/acquisition/outreach') { const b = await body(req); return send(res, 200, await acquisition.outreach.send(b)); }
  if (method === 'POST' && url.pathname === '/v1/acquisition/signature-request') { const b = await body(req); return send(res, 201, await acquisition.signing.createSignatureRequest(b)); }
  if (method === 'POST' && url.pathname === '/v1/mode') {
    const b = await body(req); const result = modeManager.transition(b.mode, b.actor ?? 'operator'); return send(res, 200, result);
  }
  if (method === 'GET' && url.pathname === '/v1/audit/verify') return send(res, 200, ledger.verify());

  if (method === 'POST' && url.pathname === '/v1/counterparties') {
    const b = await body(req);
    if (!b.name || !b.id || !['UNVERIFIED','VERIFIED'].includes(b.identityStatus ?? 'UNVERIFIED') || !Array.isArray(b.evidence)) throw new MFlowError('INVALID_COUNTERPARTY', 'id, name and evidence[] are required');
    const verifiedEvidence = b.evidence.map(verifyEvidence);
    if (b.trustedPublicKeyFingerprints && !Array.isArray(b.trustedPublicKeyFingerprints)) throw new MFlowError('INVALID_COUNTERPARTY', 'trustedPublicKeyFingerprints must be an array');
    if (b.identityStatus === 'VERIFIED') {
      if (!Number.isInteger(b.creditRiskScore) || b.creditRiskScore < 0 || b.creditRiskScore > 10000) throw new MFlowError('INVALID_COUNTERPARTY', 'Verified counterparties require creditRiskScore 0..10000');
      if (!b.trustedPublicKeyFingerprints?.length) throw new MFlowError('INVALID_COUNTERPARTY', 'Verified counterparties require trustedPublicKeyFingerprints[]');
      for (const e of verifiedEvidence) if (!b.trustedPublicKeyFingerprints.includes(e.publicKeyFingerprint)) throw new MFlowError('UNTRUSTED_EVIDENCE_KEY', 'Evidence signer fingerprint is not in the counterparty trust set', 422);
    }
    const row = counterparties.insert({ id: b.id, name: b.name, identityStatus: b.identityStatus ?? 'UNVERIFIED', creditRiskScore: b.creditRiskScore ?? null, trustedPublicKeyFingerprints: b.trustedPublicKeyFingerprints ?? [], evidence: verifiedEvidence });
    ledger.append('COUNTERPARTY_VERIFIED', { counterpartyId: row.id, evidenceHashes: verifiedEvidence.map((e) => e.sha256) });
    return send(res, 201, row);
  }

  if (method === 'POST' && url.pathname === '/v1/opportunities') {
    const b = await body(req); validateOpportunity(b);
    const cp = counterparties.get(b.counterpartyId);
    if (!cp) throw new MFlowError('COUNTERPARTY_NOT_FOUND', 'Counterparty must be registered before opportunity ingestion', 404);
    const row = opportunities.insert({ ...b, state: 'DISCOVERED' });
    const score = scoreOpportunity(row, process.env.M_FLOW_VELOCITY_TARGET_PER_DAY ?? '0.01');
    if (!score.marginPass) ledger.append('OPPORTUNITY_MARGIN_WARNING', { opportunityId: row.id, netMargin: score.netMargin, marginThreshold: score.marginThreshold });
    opportunities.update(row.id, { score, scoreVersion: 1 });
    ledger.append('OPPORTUNITY_INGESTED', { opportunityId: row.id, counterpartyId: row.counterpartyId });
    ledger.append('SCORE_RECALCULATED', { opportunityId: row.id, score });
    return send(res, 201, opportunities.get(row.id));
  }

  const opportunityMatch = url.pathname.match(/^\/v1\/opportunities\/([^/]+)\/score$/);
  if (method === 'POST' && opportunityMatch) {
    const row = opportunities.get(opportunityMatch[1]); if (!row) throw new MFlowError('OPPORTUNITY_NOT_FOUND', 'Opportunity not found', 404);
    const score = scoreOpportunity(row, process.env.M_FLOW_VELOCITY_TARGET_PER_DAY ?? '0.01');
    opportunities.update(row.id, { score, scoreVersion: (row.scoreVersion ?? 0) + 1 });
    ledger.append('SCORE_RECALCULATED', { opportunityId: row.id, score });
    return send(res, 200, score);
  }

  if (method === 'POST' && /^\/v1\/opportunities\/[^/]+\/state$/.test(url.pathname)) {
    const id = url.pathname.split('/')[3]; const b = await body(req); const row = opportunities.get(id);
    if (!row) throw new MFlowError('OPPORTUNITY_NOT_FOUND', 'Opportunity not found', 404);
    if (!OPPORTUNITY_STATES.includes(b.state)) throw new MFlowError('INVALID_OPPORTUNITY_STATE', 'Unknown opportunity state');
    if (b.state !== 'FAILED' && OPPORTUNITY_STATES.indexOf(b.state) < OPPORTUNITY_STATES.indexOf(row.state)) throw new MFlowError('INVALID_OPPORTUNITY_STATE', 'Opportunity state cannot move backward', 409);
    const updated = opportunities.update(id, { state: b.state, stateReason: b.reason ?? null });
    ledger.append('OPPORTUNITY_STATE_CHANGED', { opportunityId: id, from: row.state, to: b.state, reason: b.reason ?? null });
    return send(res, 200, updated);
  }

  if (method === 'POST' && url.pathname === '/v1/providers') {
    const b = await body(req);
    if (!b.id || !b.name || !b.rail || !b.credentialsEnv || !Array.isArray(b.credentialsEnv)) throw new MFlowError('INVALID_PROVIDER', 'id, name, rail and credentialsEnv[] are required');
    if (!b.currencies || !Array.isArray(b.currencies) || b.currencies.some((x) => !/^[A-Z]{3}$/.test(x))) throw new MFlowError('INVALID_PROVIDER', 'currencies[] must contain 3-letter uppercase currency codes');
    if (b.minCapital != null) FixedDecimal.from(b.minCapital);
    if (b.maxCapital != null) FixedDecimal.from(b.maxCapital);
    if (b.rail === 'paypal' && b.credentialsEnv.some((x) => !['PAYPAL_CLIENT_ID','PAYPAL_CLIENT_SECRET'].includes(x))) throw new MFlowError('INVALID_PROVIDER', 'PayPal provider credentials must reference known environment variables');
    const row = providers.insert({ id: b.id, name: b.name, rail: b.rail, credentialsEnv: b.credentialsEnv, currencies: b.currencies, regions: b.regions ?? [], minCapital: b.minCapital ?? null, maxCapital: b.maxCapital ?? null, evidenceUrls: b.evidenceUrls ?? [], verifiedBy: b.verifiedBy ?? null, verified: Boolean(b.verified), active: Boolean(b.active) });
    ledger.append('PROVIDER_REGISTERED', { providerId: row.id, rail: row.rail, active: row.active });
    return send(res, 201, row);
  }

  if (method === 'POST' && url.pathname === '/v1/provider-matches') {
    const b = await body(req);
    validateSettlementInput(b);
    const matches = matchProviders(providers.all(), { amount: b.amount, currency: b.currency, region: b.region });
    ledger.append('PROVIDER_MATCH_REQUESTED', { amount: b.amount, currency: b.currency, region: b.region ?? null, matchCount: matches.length });
    return send(res, 200, { matches: matches.map(({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active }) => ({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active })) });
  }

  if (method === 'POST' && url.pathname === '/v1/settlement-authorizations') {
    const b = await body(req);
    validateSettlementInput(b);
    const opportunity = opportunities.get(b.opportunityId); const cp = opportunity ? counterparties.get(opportunity.counterpartyId) : null;
    if (!opportunity || !cp) throw new MFlowError('NOT_FOUND', 'Opportunity/counterparty not found', 404);
    if (!['PAPER','LIVE'].includes(modeManager.get())) throw new MFlowError('INVALID_MODE', 'Settlement authorization requires PAPER or LIVE mode', 409);
    validateSettlementInput(b);
    if (cp.identityStatus !== 'VERIFIED') throw new MFlowError('LIVE_GATE_IDENTITY', 'Verified counterparty identity is required', 423);
    if (opportunity.score?.marginPass !== true) throw new MFlowError('LIVE_GATE_MARGIN', 'Opportunity must pass its configured margin threshold before authorization', 423);
    const provider = b.providerId ? providers.get(b.providerId) : null;
    if (!provider || !provider.active || !provider.verified) throw new MFlowError('PROVIDER_NOT_AUTHORIZED', 'providerId must reference an active, verified provider', 423);
    if (provider.rail !== 'paypal') throw new MFlowError('RAIL_NOT_IMPLEMENTED', `No adapter registered for ${provider.rail}`, 501);
    const verifiedEvidence = cp.evidence.filter((e) => e.verified === true);
    if (!verifiedEvidence.some((e) => ['KYC','AML','IDENTITY'].includes(String(e.type).toUpperCase())) || !verifiedEvidence.some((e) => ['INVOICE','CONTRACT','PURCHASE_ORDER'].includes(String(e.type).toUpperCase()))) throw new MFlowError('LIVE_GATE_EVIDENCE_SET', 'Authorization requires verified identity/risk evidence plus verified transaction evidence', 423);
    const authorizationHash = crypto.createHash('sha256').update(canonicalize({ opportunityId: opportunity.id, counterpartyId: cp.id, providerId: b.providerId ?? null, amount: b.amount, currency: b.currency, purpose: b.purpose, authorizedAt: new Date().toISOString() })).digest('hex');
    ledger.append('SETTLEMENT_AUTHORIZED', { authorizationHash, opportunityId: opportunity.id, counterpartyId: cp.id, providerId: b.providerId ?? null, providerRail: provider.rail, amount: b.amount, currency: b.currency });
    return send(res, 201, { authorizationHash });
  }

  if (method === 'POST' && url.pathname === '/v1/executions') {
    const b = await body(req);
    validateSettlementInput(b);
    const opportunity = opportunities.get(b.opportunityId); const cp = opportunity ? counterparties.get(opportunity.counterpartyId) : null;
    if (!opportunity || !cp) throw new MFlowError('NOT_FOUND', 'Opportunity/counterparty not found', 404);
    liveExecutionGate(opportunity, cp, b.authorizationHash, b);
    if (ledger.hasEvent('EXECUTION_SUBMITTED', (p) => p.authorizationHash === b.authorizationHash)) throw new MFlowError('AUTHORIZATION_ALREADY_USED', 'Settlement authorization has already produced a submitted execution', 409);
    if (b.rail && b.rail !== 'paypal') throw new MFlowError('RAIL_NOT_IMPLEMENTED', `No adapter registered for ${b.rail}`, 501);
    const provider = providers.get(b.providerId);
    if (!provider || !provider.active || !provider.verified || provider.rail !== (b.rail ?? 'paypal')) throw new MFlowError('PROVIDER_NOT_AUTHORIZED', 'providerId must reference an active, verified provider matching the settlement rail', 423);
    if (typeof b.recipient !== 'string' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(b.recipient)) throw new MFlowError('INVALID_SETTLEMENT', 'PayPal recipient must be a valid email address');
    const attempt = executions.insert({ opportunityId: opportunity.id, rail: b.rail ?? 'paypal', amount: b.amount, currency: b.currency, recipient: b.recipient, authorizationHash: b.authorizationHash, status: 'ATTEMPTED' });
    ledger.append('EXECUTION_ATTEMPTED', { executionId: attempt.id, opportunityId: opportunity.id, rail: b.rail ?? 'paypal', authorizationHash: b.authorizationHash, amount: b.amount, currency: b.currency });
    try {
      const adapter = b.rail === 'paypal' || !b.rail ? paypal : null;
      if (!adapter) throw new MFlowError('RAIL_NOT_IMPLEMENTED', `No adapter registered for ${b.rail}` , 501);
      const result = await adapter.execute({ amount: b.amount, currency: b.currency, recipient: b.recipient, idempotencyKey: b.authorizationHash, subject: 'M-FLOW authorized settlement', note: b.note });
      executions.update(attempt.id, { status: 'SUBMITTED', result });
      ledger.append('EXECUTION_SUBMITTED', { executionId: attempt.id, opportunityId: opportunity.id, rail: adapter.name, externalReference: result.externalReference ?? null });
      return send(res, 200, { executionId: attempt.id, status: 'SUBMITTED', result });
    } catch (error) {
      executions.update(attempt.id, { status: 'FAILED', errorCode: error.code ?? 'EXECUTION_FAILED', error: error.message });
      ledger.append('EXECUTION_FAILED', { executionId: attempt.id, opportunityId: opportunity.id, code: error.code ?? 'EXECUTION_FAILED' });
      throw error;
    }
  }

  if (method === 'POST' && url.pathname.endsWith('/documents')) {
    const id = url.pathname.split('/')[3]; const opportunity = opportunities.get(id); if (!opportunity) throw new MFlowError('OPPORTUNITY_NOT_FOUND', 'Opportunity not found', 404);
    const score = opportunity.score ?? scoreOpportunity(opportunity, process.env.M_FLOW_VELOCITY_TARGET_PER_DAY ?? '0.01');
    const term = buildTermsSheet(opportunity, score); const contract = buildCapitalDistributionContract(opportunity, score);
    const row = documents.insert({ opportunityId: id, termsSheet: term, capitalDistributionContract: contract });
    ledger.append('DOCUMENT_GENERATED', { opportunityId: id, documentIds: [row.id], documentHashes: [term.sha256, contract.sha256] });
    return send(res, 201, row);
  }

  return send(res, 404, { error: 'NOT_FOUND', message: 'Route not found' });
}

const port = Number(process.env.M_FLOW_PORT ?? 8787);
const host = process.env.M_FLOW_HOST ?? '127.0.0.1';
const server = http.createServer((req, res) => route(req, res).catch((error) => {
  if (error instanceof MFlowError) return send(res, error.status, { error: error.code, message: error.message });
  log('error', 'request.failed', { message: error.message });
  return send(res, 500, { error: 'INTERNAL_ERROR', message: 'Internal server error' });
}));
server.listen(port, host, () => {
  acquisition.startScheduler();
  log('info', 'mflow.started', { host, port, mode: modeManager.get(), node: process.version, acquisitionAutomation: process.env.M_FLOW_AUTODISCOVERY_ENABLED === 'true' });
});

