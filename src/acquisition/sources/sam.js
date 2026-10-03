import { fetchJson, requiredEnv, cleanText } from '../net.js';

const URL = 'https://api.sam.gov/opportunities/v2/search';

function isoDate(daysBack) {
  const d = new Date(Date.now() - daysBack * 86400000);
  return `${String(d.getUTCMonth()+1).padStart(2,'0')}/${String(d.getUTCDate()).padStart(2,'0')}/${d.getUTCFullYear()}`;
}

export class SamSource {
  name = 'sam.gov';
  status() {
    return { enabled: Boolean(process.env.SAM_API_KEY?.trim()), requiresCredential: true, ready: Boolean(process.env.SAM_API_KEY?.trim()) };
  }

  async discover({ title, daysBack = 7, limit = 50 } = {}) {
    const apiKey = requiredEnv('SAM_API_KEY');
    const params = new URLSearchParams({
      api_key: apiKey,
      postedFrom: isoDate(Math.max(0, Number(daysBack) || 7)),
      postedTo: isoDate(0),
      limit: String(Math.min(Math.max(Number(limit) || 50, 1), 1000)),
      ptype: 'o'
    });
    if (title?.trim()) params.set('title', title.trim());
    const data = await fetchJson(`${URL}?${params.toString()}`);
    return (data.opportunitiesData ?? []).map((o) => {
      const poc = Array.isArray(o.pointOfContact) ? o.pointOfContact.find((p) => p?.email) ?? o.pointOfContact[0] : null;
      return {
        source: this.name,
        sourceId: String(o.noticeId ?? o.solicitationNumber ?? ''),
        title: cleanText(o.title),
        buyerName: cleanText(o.fullParentPathName ?? o.department ?? ''),
        buyerCountry: 'USA',
        sector: [String(o.naicsCode ?? '').trim()].filter(Boolean),
        estimatedValue: String(o.award?.amount ?? ''),
        currency: 'USD',
        deadline: String(o.responseDeadLine ?? ''),
        url: o.uiLink ?? null,
        buyerContact: poc ? { name: cleanText(poc.fullName ?? ''), email: cleanText(poc.email ?? ''), phone: cleanText(poc.phone ?? '') } : null,
        awardee: o.award?.awardee ?? null,
        raw: o
      };
    }).filter((x) => x.sourceId && x.title);
  }
}
