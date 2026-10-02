import fs from 'node:fs';
import { MFlowError } from '../core/errors.js';

const ORDER = { DISCOVERY: 0, PAPER: 1, LIVE: 2 };

export class ModeManager {
  constructor(file, ledger, liveGateChecker) {
    this.file = file;
    this.ledger = ledger;
    this.liveGateChecker = liveGateChecker;
    if (!fs.existsSync(file)) {
      const requested = process.env.M_FLOW_MODE ?? 'DISCOVERY';
      if (requested === 'LIVE') {
        const gates = this.liveGateChecker();
        if (!(gates.providerCredentials && gates.counterpartyEvidence && gates.settlementAuthorization)) {
          this.#write('DISCOVERY');
          this.ledger.append('MODE_FAIL_CLOSED', { requested: 'LIVE', active: 'DISCOVERY', gates });
        } else this.#write('LIVE');
      } else if (requested === 'PAPER' || requested === 'DISCOVERY') this.#write(requested);
      else this.#write('DISCOVERY');
    } else if (this.#read() === 'LIVE') {
      const gates = this.liveGateChecker();
      if (!(gates.providerCredentials && gates.counterpartyEvidence && gates.settlementAuthorization)) {
        this.#write('DISCOVERY');
        this.ledger.append('MODE_FAIL_CLOSED', { requested: 'LIVE', active: 'DISCOVERY', gates, reason: 'startup gate revalidation' });
      }
    }
  }
  #read() { return JSON.parse(fs.readFileSync(this.file, 'utf8')).mode; }
  #write(mode) { fs.writeFileSync(this.file, JSON.stringify({ mode, updatedAt: new Date().toISOString() }, null, 2) + '\n'); }
  get() { return this.#read(); }
  transition(target, actor = 'operator') {
    const current = this.get();
    if (target === current) return { mode: current, changed: false };
    if (target === 'DISCOVERY') {
      this.#write(target);
      this.ledger.append('MODE_CHANGED', { from: current, to: target, actor, reason: 'fail-closed demotion' });
      return { mode: target, changed: true };
    }
    if (!(target in ORDER) || ORDER[target] !== ORDER[current] + 1) throw new MFlowError('INVALID_MODE_TRANSITION', `Allowed forward transitions are DISCOVERY -> PAPER -> LIVE; requested ${current} -> ${target}`, 409);
    if (target === 'LIVE') {
      const gates = this.liveGateChecker();
      if (!gates.providerCredentials || !gates.counterpartyEvidence || !gates.settlementAuthorization) {
        throw new MFlowError('LIVE_GATES_FAILED', 'LIVE mode is blocked because one or more required gates are not satisfied', 423);
      }
    }
    this.#write(target);
    this.ledger.append('MODE_CHANGED', { from: current, to: target, actor });
    return { mode: target, changed: true };
  }
}
