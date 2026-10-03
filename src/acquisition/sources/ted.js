import { fetchJson, cleanText } from '../net.js';

const URL = 'https://api.ted.europa.eu/v3/notices/search';

function firstText(v) {
  if (!v) return '';
  if (typeof v === 'string') return cleanText(v);
  if (Array.isArray(v)) return firstText(v[0]);
  if (typeof v === 'object') return firstText(v.eng ?? Object.values(v)[0]);
  return String(v);
}

export class TedSource {
  name = 'ted';
  status() {
    return { enabled: Boolean(process.env.M_FLOW_TED_QUERY?.trim()), requiresCredential: false, ready: Boolean(process.env.M_FLOW_TED_QUERY?.trim()) };
  }

  async discover({ query = process.env.M_FLOW_TED_QUERY, limit = 50 } = {}) {
    if (!query?.trim()) throw new Error('MISSING_ENV:M_FLOW_TED_QUERY');
    const body = {
      query: query.trim(),
      fields: [
        'publication-number', 'notice-title', 'notice-type', 'buyer-name',
        'buyer-country', 'classification-cpv', 'estimated-value-proc',
        'estimated-value-cur-proc', 'deadline-receipt-tender-date'
      ],
      page: 1,
      limit: Math.min(Math.max(Number(limit) || 50, 1), 250),
      scope: 'ACTIVE',
      paginationMode: 'PAGE_NUMBER',
      onlyLatestVersions: true
    };
    const data = await fetchJson(URL, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return (data.notices ?? []).map((n) => ({
      source: this.name,
      sourceId: String(n['publication-number'] ?? n.publicationNumber ?? ''),
      title: firstText(n['notice-title'] ?? n.title),
      buyerName: firstText(n['buyer-name'] ?? n.buyerName),
      buyerCountry: firstText(n['buyer-country'] ?? n.buyerCountry),
      sector: Array.isArray(n['classification-cpv']) ? n['classification-cpv'].map(String) : [String(n['classification-cpv'] ?? '')].filter(Boolean),
      estimatedValue: String(n['estimated-value-proc'] ?? ''),
      currency: String(n['estimated-value-cur-proc'] ?? ''),
      deadline: firstText(n['deadline-receipt-tender-date'] ?? n.deadline),
      url: n.links?.[0]?.href ?? (n['publication-number'] ? `https://ted.europa.eu/en/notice/-/detail/${n['publication-number']}` : null),
      raw: n
    })).filter((x) => x.sourceId && x.title);
  }
}
