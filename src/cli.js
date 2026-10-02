import path from 'node:path';
import { AuditLedger } from './ledger/ledger.js';
import fs from 'node:fs';
const dataDir = path.resolve(process.env.M_FLOW_DATA_DIR ?? './data');
fs.mkdirSync(dataDir, { recursive: true });
const ledger = new AuditLedger(path.resolve(process.env.M_FLOW_LEDGER_FILE ?? path.join(dataDir, 'ledger.jsonl')));
const command = process.argv[2];
if (command === 'audit') console.log(JSON.stringify(ledger.verify(), null, 2));
else if (command === 'mode') console.log(process.env.M_FLOW_MODE ?? 'DISCOVERY');
else { console.error('Usage: node src/cli.js audit|mode'); process.exitCode = 2; }
