import crypto from 'node:crypto';
import { TedSource } from './sources/ted.js';
import { SamSource } from './sources/sam.js';
import { OpenCorporatesSource } from './sources/opencorporates.js';
import { SumsubKYB } from './kyb/sumsub.js';
import { SignWellAdapter } from './signing/signwell.js';
import { ResendOutreach } from './outreach/resend.js';
import { ETimsVerifier } from './invoice/etims.js';
import { loadAuthorizedCapitalFeed } from './providers/feed.js';
import { JsonStore } from '../data/store.js';
import { FixedDecimal } from '../finance/decimal.js';
import { canonicalize } from '../core/canonical.js';

function text(value) { return typeof value === 'string' ? value.trim() : ''; }
function idFor(source, sourceId) { return `ACQ-${crypto.createHash('sha256').update(`${source}|${sourceId}`).digest('hex').slice(0,24)}`; }
function valueToFixed(value) {
  const v = text(value);
  if (!v || !/^\d+(?:\.\d{1,6})?$/.test(v)) return null;
  try { return FixedDecimal.from(v).toString(); } catch { return null; }
}

export class AcquisitionEngine {
  constructor({ ledger, providerStore }) {
    this.ledger = ledger;
    this.providerStore = providerStore;
    this.opportunities = new JsonStore('data/acquisition-opportunities.json');
    this.contacts = new JsonStore('data/acquisition-contacts.json');
    this.deals = new JsonStore('data/acquisition-deals.json');
    this.sources = [new TedSource(), new SamSource()];
    this.registrySearch = new OpenCorporatesSource();
    this.kyb = new SumsubKYB();
    this.signing = new SignWellAdapter();
    this.outreach = new ResendOutreach();
    this.etims = new ETimsVerifier();
    this.timer = null;
    this.remoteCapitalProviders = [];
  }

  sourceStatus() {
    return {
      ted: this.sources[0].status(),
      sam: this.sources[1].status(),
      opencorporates: this.registrySearch.status(),
      sumsub: this.kyb.status(),
      signwell: this.signing.status(),
      resend: this.outreach.status(),
      kenyaOfficialFeed: { enabled: Boolean(process.env.M_FLOW_KENYA_PROCUREMENT_FEED_URL?.trim()), configured: Boolean(process.env.M_FLOW_KENYA_PROCUREMENT_FEED_URL?.trim()) },
      capitalProviderFeed: { enabled: Boolean(process.env.M_FLOW_CAPITAL_PROVIDER_FEED_URL?.trim()), configured: Boolean(process.env.M_FLOW_CAPITAL_PROVIDER_FEED_URL?.trim()) },
      etims: this.etims.status()
    };
  }

  normalize(item) {
    const id = idFor(item.source, item.sourceId);
    return {
      id, source: item.source, sourceId: item.sourceId, title: text(item.title),
      buyerCandidate: item.buyerName ? { name: text(item.buyerName), country: text(item.buyerCountry), contact: item.buyerContact ?? null } : null,
      supplierCandidates: [],
      sector: Array.isArray(item.sector) ? item.sector.filter(Boolean).map(String) : [],
      estimatedValue: valueToFixed(item.estimatedValue), currency: text(item.currency).toUpperCase() || null,
      deadline: text(item.deadline) || null, url: item.url ?? null,
      status: 'DISCOVERED', evidence: { sourceRecordHash: crypto.createHash('sha256').update(canonicalize(item.raw ?? item)).digest('hex') },
      consent: { buyer: false, supplier: false, capitalProvider: false },
      sourceData: item.raw ?? item
    };
  }

  async discover({ tedQuery, samTitle, daysBack = 7, limit = 25 } = {}) {
    const discovered = [];
    const errors = [];
    for (const source of this.sources) {
      try {
        const items = source.name === 'ted' ? await source.discover({ query: tedQuery, limit }) : await source.discover({ title: samTitle, daysBack, limit });
        for (const item of items) {
          const row = this.normalize(item);
          const existing = this.opportunities.get(row.id);
          if (existing) {
            this.opportunities.update(row.id, { ...row, status: existing.status });
          } else {
            this.opportunities.insert(row);
          }
          discovered.push(this.opportunities.get(row.id));
          this.ledger.append('ACQUISITION_OPPORTUNITY_DISCOVERED', { opportunityId: row.id, source: row.source, sourceId: row.sourceId });
        }
      } catch (error) {
        errors.push({ source: source.name, code: error.message?.startsWith('MISSING_ENV:') ? error.message : 'SOURCE_UNAVAILABLE' });
        this.ledger.append('ACQUISITION_SOURCE_ERROR', { source: source.name, errorCode: errors.at(-1).code });
      }
    }

    if (process.env.M_FLOW_KENYA_PROCUREMENT_FEED_URL?.trim()) {
      try {
        const feed = await fetch(process.env.M_FLOW_KENYA_PROCUREMENT_FEED_URL, { headers: { accept:'application/json', 'user-agent':process.env.M_FLOW_USER_AGENT ?? 'M-FLOW/1.0' } });
        if (!feed.ok) throw new Error('KENYA_FEED_HTTP');
        const data = await feed.json();
        for (const item of (Array.isArray(data) ? data : data.opportunities ?? data.items ?? [])) {
          const row = this.normalize({ source:'kenya-official-feed', sourceId:String(item.id ?? item.noticeId ?? item.reference ?? ''), title:item.title ?? item.description, buyerName:item.buyerName ?? item.entityName, buyerCountry:'KEN', estimatedValue:item.amount ?? item.estimatedValue, currency:item.currency ?? 'KES', deadline:item.deadline, url:item.url, raw:item });
          if (!row.sourceId || !row.title) continue;
          if (!this.opportunities.get(row.id)) this.opportunities.insert(row);
          discovered.push(this.opportunities.get(row.id));
          this.ledger.append('ACQUISITION_OPPORTUNITY_DISCOVERED', { opportunityId: row.id, source: row.source, sourceId: row.sourceId });
        }
      } catch {
        errors.push({ source:'kenya-official-feed', code:'SOURCE_UNAVAILABLE' });
      }
    }

    return { discovered, errors, counts: { discovered: discovered.length, errors: errors.length } };
  }

  async supplierCandidates({ opportunityId, query, jurisdictionCode, limit = 10 } = {}) {
    const opportunity = opportunityId ? this.opportunities.get(opportunityId) : null;
    const q = query ?? opportunity?.title;
    if (!q) throw new Error('SUPPLIER_SEARCH_QUERY_REQUIRED');
    const candidates = await this.registrySearch.search({ q, jurisdictionCode, limit });
    if (opportunity) {
      this.opportunities.update(opportunity.id, { supplierCandidates: candidates });
      this.ledger.append('ACQUISITION_SUPPLIER_CANDIDATES_FOUND', { opportunityId: opportunity.id, count: candidates.length });
    }
    return candidates;
  }

  capitalMatches({ amount, currency, region }) {
    const requested = FixedDecimal.from(amount);
    const compatible = (p) => p.active === true && p.verified === true && p.authorized !== false
      && (!Array.isArray(p.currencies) || p.currencies.includes(currency))
      && (!region || !Array.isArray(p.regions) || !p.regions.length || p.regions.includes(region))
      && (p.minCapital == null || requested.gte(p.minCapital))
      && (p.maxCapital == null || requested.lte(p.maxCapital));
    const local = (this.providerStore?.all?.() ?? []).filter(compatible).map(({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active }) => ({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active, source:'m-flow-provider-registry' }));
    const remote = (this.remoteCapitalProviders ?? []).filter(compatible).map(({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active }) => ({ id, name, rail, currencies, regions, minCapital, maxCapital, verified, active, source:'authorized-provider-feed' }));
    const byId = new Map([...local, ...remote].map((p) => [p.id, p]));
    return [...byId.values()];
  }

  async refreshCapitalFeed() {
    try {
      const remote = await loadAuthorizedCapitalFeed();
      this.remoteCapitalProviders = remote;
      return { count: remote.length, providers: remote.map(({ id, name, rail, currencies, regions, minCapital, maxCapital }) => ({ id, name, rail, currencies, regions, minCapital, maxCapital, source:'authorized-provider-feed' })) };
    } catch (error) {
      this.ledger.append('ACQUISITION_CAPITAL_FEED_ERROR', { errorCode: error.message?.slice(0,80) ?? 'ERROR' });
      return { count: 0, providers: [], error: 'SOURCE_UNAVAILABLE' };
    }
  }

  async verifyInvoice(invoice) {
    const result = await this.etims.verify(invoice);
    this.ledger.append('ACQUISITION_INVOICE_VERIFIED', { verificationReference: result?.id ?? result?.reference ?? null });
    return result;
  }

  startScheduler() {
    if (process.env.M_FLOW_AUTODISCOVERY_ENABLED !== 'true') return false;
    const interval = Math.max(60, Number(process.env.M_FLOW_DISCOVERY_INTERVAL_SECONDS ?? 900));
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(async () => {
      try {
        const result = await this.discover({ limit: Number(process.env.M_FLOW_DISCOVERY_LIMIT ?? 25) });
        if (process.env.OPENCORPORATES_API_TOKEN?.trim()) {
          for (const opportunity of result.discovered.slice(0, 10)) {
            try { await this.supplierCandidates({ opportunityId: opportunity.id, limit: 10 }); } catch {}
          }
        }
        await this.refreshCapitalFeed();
      } catch (e) {
        this.ledger.append('ACQUISITION_SCHEDULER_ERROR', { errorCode: e.message?.slice(0,80) ?? 'ERROR' });
      }
    }, interval * 1000);
    this.timer.unref?.();
    return true;
  }
}
