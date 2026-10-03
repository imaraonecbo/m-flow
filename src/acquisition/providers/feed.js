import { fetchJson } from '../net.js';

export async function loadAuthorizedCapitalFeed() {
  const url = process.env.M_FLOW_CAPITAL_PROVIDER_FEED_URL?.trim();
  if (!url) return [];
  const data = await fetchJson(url, { headers: { 'x-m-flow-client': 'acquisition-engine' } });
  const rows = Array.isArray(data) ? data : (data.providers ?? data.items ?? []);
  return rows.filter((p) => p && p.verified === true && p.authorized === true && p.id && p.name && p.rail);
}
