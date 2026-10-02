import crypto from 'node:crypto';
import { canonicalize } from '../core/canonical.js';
import { MFlowError } from '../core/errors.js';

export function sha256Hex(value) {
  const input = Buffer.isBuffer(value) ? value : Buffer.from(typeof value === 'string' ? value : canonicalize(value), 'utf8');
  return crypto.createHash('sha256').update(input).digest('hex');
}

export function verifyEvidence(evidence) {
  if (!evidence || typeof evidence !== 'object') throw new MFlowError('INVALID_EVIDENCE', 'Evidence object required');
  for (const key of ['type','payload','sha256','signatureBase64','publicKeyPem']) if (!evidence[key]) throw new MFlowError('INVALID_EVIDENCE', `${key} is required`);
  const canonicalPayload = canonicalize(evidence.payload);
  const actualHash = sha256Hex(canonicalPayload);
  if (actualHash !== evidence.sha256.toLowerCase()) throw new MFlowError('EVIDENCE_HASH_MISMATCH', 'Evidence payload hash does not match');
  let signature;
  try { signature = Buffer.from(evidence.signatureBase64, 'base64'); } catch { throw new MFlowError('INVALID_EVIDENCE_SIGNATURE', 'Invalid base64 signature'); }
  const publicKey = crypto.createPublicKey(evidence.publicKeyPem);
  const valid = crypto.verify(null, Buffer.from(canonicalPayload), publicKey, signature);
  if (!valid) throw new MFlowError('INVALID_EVIDENCE_SIGNATURE', 'Evidence signature verification failed');
  const spkiDer = publicKey.export({ type: 'spki', format: 'der' });
  const fingerprint = sha256Hex(spkiDer);
  return { ...evidence, verified: true, verifiedAt: new Date().toISOString(), canonicalPayloadHash: actualHash, publicKeyFingerprint: fingerprint };
}

export function generateEd25519KeyPair() {
  return crypto.generateKeyPairSync('ed25519');
}
