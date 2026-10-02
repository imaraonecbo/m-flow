const SECRET_KEYS = /(secret|token|password|private.?key|authorization|client_secret|api.?key)/i;

function sanitize(value) {
  if (Array.isArray(value)) return value.map(sanitize);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = SECRET_KEYS.test(k) ? '[REDACTED]' : sanitize(v);
    return out;
  }
  return value;
}

export function log(level, event, details = {}) {
  process.stdout.write(`${JSON.stringify({ ts: new Date().toISOString(), level, event, ...sanitize(details) })}\n`);
}
