import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { Money } from '../src/finance/decimal.js';
import { scoreOpportunity } from '../src/risk/scoring.js';
import { verifyEvidence, sha256Hex } from '../src/crypto/evidence.js';
import { canonicalize } from '../src/core/canonical.js';
import { AuditLedger } from '../src/ledger/ledger.js';
import { ModeManager } from '../src/mode/state.js';

const opportunity = {
  volume: '100', unitPrice: '125.50', leadTimeDays: '3', counterpartyId: 'cp-1', marginThreshold: '0.10',
  cogs: '9000.00', financingCost: '500.00', platformFee: '25.00', requiredCapital: '9500.00', durationDays: '5', counterpartyScore: 9000, volatilityFactor: 1000
};

test('fixed precision money never uses binary floating arithmetic', () => {
  assert.equal(Money.from('0.10').add('0.20').toString(), '0.300000');
  assert.equal(Money.from('100.00').sub('0.01').toString(), '99.990000');
});

test('financial calculations use the specified formula', () => {
  const score = scoreOpportunity(opportunity, '0.01');
  assert.equal(score.grossRevenue, '12550.000000');
  assert.equal(score.netProfit, '3025.000000');
  assert.equal(score.capitalVelocity, '0.063684');
  assert.equal(score.velocityScoreBps, 10000);
  assert.equal(score.riskAdjustedScore, 7400);
  assert.equal(score.marginPass, true);
});

test('signed evidence is cryptographically verified', () => {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  const payload = { documentId: 'INV-TEST-001', amount: '12550.000000', counterpartyId: 'cp-1' };
  const canonical = canonicalize(payload);
  const signature = crypto.sign(null, Buffer.from(canonical), privateKey).toString('base64');
  const evidence = { type: 'INVOICE', payload, sha256: sha256Hex(canonical), signatureBase64: signature, publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }) };
  assert.equal(verifyEvidence(evidence).verified, true);
  assert.match(verifyEvidence(evidence).publicKeyFingerprint, /^[0-9a-f]{64}$/);
});

test('ledger is hash chained and detects tampering', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mflow-ledger-'));
  const ledgerPath = path.join(dir, 'ledger.jsonl');
  const ledger = new AuditLedger(ledgerPath);
  ledger.append('A', { n: 1 });
  ledger.append('B', { n: 2 });
  assert.equal(ledger.verify().valid, true);
  const lines = fs.readFileSync(ledgerPath, 'utf8').trim().split('\n');
  const row = JSON.parse(lines[0]); row.payload.n = 999; lines[0] = JSON.stringify(row); fs.writeFileSync(ledgerPath, lines.join('\n') + '\n');
  assert.equal(ledger.verify().valid, false);
});

test('mode manager blocks LIVE unless all gates pass', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mflow-mode-'));
  const ledger = new AuditLedger(path.join(dir, 'ledger.jsonl'));
  const manager = new ModeManager(path.join(dir, 'mode.json'), ledger, () => ({ providerCredentials: true, counterpartyEvidence: true, settlementAuthorization: false }));
  manager.transition('PAPER');
  assert.throws(() => manager.transition('LIVE'), /LIVE mode is blocked/);
});


test('margin threshold fails closed', () => {
  const lowMargin = { ...opportunity, marginThreshold: '0.30' };
  const score = scoreOpportunity(lowMargin, '0.01');
  assert.equal(score.marginPass, false);
});

test('LIVE cannot be selected at startup without all gates', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mflow-live-start-'));
  const ledger = new AuditLedger(path.join(dir, 'ledger.jsonl'));
  process.env.M_FLOW_MODE = 'LIVE';
  try {
    const manager = new ModeManager(path.join(dir, 'mode.json'), ledger, () => ({ providerCredentials: false, counterpartyEvidence: false, settlementAuthorization: false }));
    assert.equal(manager.get(), 'DISCOVERY');
    assert.equal(ledger.entries()[0].eventType, 'MODE_FAIL_CLOSED');
  } finally { delete process.env.M_FLOW_MODE; }
});
