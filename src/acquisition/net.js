import crypto from 'node:crypto';

export async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 20000);
  try {
    const { timeoutMs, ...fetchOptions } = options;
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        accept: 'application/json',
        'user-agent': process.env.M_FLOW_USER_AGENT ?? 'M-FLOW/1.0 (+https://github.com/imaraonecbo/m-flow)',
        ...(fetchOptions.headers ?? {})
      }
    });
    if (!response.ok) throw new Error(`REMOTE_HTTP_${response.status}`);
    return await response.json();
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('REMOTE_TIMEOUT');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function sha256Canonical(value, canonicalize) {
  return crypto.createHash('sha256').update(canonicalize(value)).digest('hex');
}

export function requiredEnv(name) {
  const value = process.env[name];
  if (!value?.trim()) throw new Error(`MISSING_ENV:${name}`);
  return value.trim();
}

export function envPresent(...names) {
  return Object.fromEntries(names.map((n) => [n, Boolean(process.env[n]?.trim())]));
}

export function cleanText(value) {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
}
