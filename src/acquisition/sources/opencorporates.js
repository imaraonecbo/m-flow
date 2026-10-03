import { fetchJson, requiredEnv, cleanText } from '../net.js';

const BASE = 'https://api.opencorporates.com/v0.4/companies/search';

export class OpenCorporatesSource {
  name = 'opencorporates';
  status() {
    return { enabled: Boolean(process.env.OPENCORPORATES_API_TOKEN?.trim()), requiresCredential: true, ready: Boolean(process.env.OPENCORPORATES_API_TOKEN?.trim()) };
  }

  async search({ q, jurisdictionCode, limit = 10 } = {}) {
    if (!q?.trim()) throw new Error('COMPANY_SEARCH_QUERY_REQUIRED');
    const token = requiredEnv('OPENCORPORATES_API_TOKEN');
    const params = new URLSearchParams({ q: q.trim(), api_token: token, per_page: String(Math.min(Math.max(Number(limit) || 10, 1), 30)) });
    if (jurisdictionCode?.trim()) params.set('jurisdiction_code', jurisdictionCode.trim().toLowerCase());
    const data = await fetchJson(`${BASE}?${params.toString()}`);
    const companies = data?.results?.companies?.map?.((x) => x.company) ?? [];
    return companies.map((c) => ({
      companyNumber: cleanText(c.company_number), name: cleanText(c.name), jurisdictionCode: cleanText(c.jurisdiction_code),
      status: cleanText(c.current_status ?? c.status), incorporationDate: c.incorporation_date ?? null,
      address: c.registered_address_in_full ?? null, url: c.opencorporates_url ?? null, raw: c
    }));
  }
}
