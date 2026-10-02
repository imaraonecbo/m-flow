import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { canonicalize } from '../core/canonical.js';
import { log } from '../core/logging.js';

const GENESIS = '0'.repeat(64);

export class AuditLedger {
  constructor(file) {
    this.file = file;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    if (!fs.existsSync(file)) fs.writeFileSync(file, '', 'utf8');
  }
  #lastHash() {
    const content = fs.readFileSync(this.file, 'utf8').trim();
    if (!content) return GENESIS;
    const lines = content.split('\n').filter(Boolean);
    const last = JSON.parse(lines.at(-1));
    return last.entryHash;
  }
  append(eventType, payload = {}) {
    const previousHash = this.#lastHash();
    const entry = { id: crypto.randomUUID(), ts: new Date().toISOString(), eventType, previousHash, payload };
    const entryHash = crypto.createHash('sha256').update(canonicalize(entry)).digest('hex');
    const full = { ...entry, entryHash };
    fs.appendFileSync(this.file, JSON.stringify(full) + '\n', 'utf8');
    log('info', 'ledger.append', { eventType, entryId: full.id, entryHash });
    return full;
  }
  entries() {
    const text = fs.readFileSync(this.file, 'utf8').trim();
    return text ? text.split('\n').filter(Boolean).map(JSON.parse) : [];
  }
  verify() {
    const rows = this.entries();
    let previous = GENESIS;
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const { entryHash, ...entry } = row;
      if (row.previousHash !== previous) return { valid: false, index: i, reason: 'previousHash mismatch', entryHash: row.entryHash };
      const expected = crypto.createHash('sha256').update(canonicalize(entry)).digest('hex');
      if (expected !== entryHash) return { valid: false, index: i, reason: 'entryHash mismatch', entryHash: row.entryHash };
      previous = entryHash;
    }
    return { valid: true, entries: rows.length, head: previous };
  }
  hasEvent(eventType, predicate = () => true) { return this.entries().some((e) => e.eventType === eventType && predicate(e.payload)); }
}
