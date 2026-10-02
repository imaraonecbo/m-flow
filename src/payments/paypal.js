import { PaymentRailAdapter } from './adapter.js';
import { MFlowError } from '../core/errors.js';

export class PayPalAdapter extends PaymentRailAdapter {
  constructor(env = process.env) {
    super('paypal');
    this.env = env;
  }
  configuredForLive() {
    return this.env.PAYPAL_ENV === 'live' && Boolean(this.env.PAYPAL_CLIENT_ID) && Boolean(this.env.PAYPAL_CLIENT_SECRET);
  }
  baseUrl() { return this.env.PAYPAL_ENV === 'live' ? 'https://api-m.paypal.com' : 'https://api-m.sandbox.paypal.com'; }
  async #accessToken() {
    if (!this.env.PAYPAL_CLIENT_ID || !this.env.PAYPAL_CLIENT_SECRET) throw new MFlowError('PAYPAL_NOT_CONFIGURED', 'PayPal client credentials are not configured', 503);
    const basic = Buffer.from(`${this.env.PAYPAL_CLIENT_ID}:${this.env.PAYPAL_CLIENT_SECRET}`).toString('base64');
    const res = await fetch(`${this.baseUrl()}/v1/oauth2/token`, {
      method: 'POST',
      headers: { Authorization: `Basic ${basic}`, 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
      body: 'grant_type=client_credentials'
    });
    if (!res.ok) throw new MFlowError('PAYPAL_AUTH_FAILED', `PayPal OAuth failed with HTTP ${res.status}`, 502);
    const body = await res.json();
    if (!body.access_token) throw new MFlowError('PAYPAL_AUTH_FAILED', 'PayPal OAuth response did not contain an access token', 502);
    return body.access_token;
  }
  async authorize(request) {
    if (!this.configuredForLive()) throw new MFlowError('PAYPAL_LIVE_GATE_FAILED', 'PayPal live credentials/environment are not configured', 423);
    return { authorized: true, rail: this.name, externalAccountConfigured: true, currency: request.currency };
  }
  async execute(request) {
    if (!this.configuredForLive()) throw new MFlowError('PAYPAL_LIVE_GATE_FAILED', 'PayPal live credentials/environment are not configured', 423);
    const token = await this.#accessToken();
    const senderBatchId = request.idempotencyKey;
    const payload = {
      sender_batch_header: { sender_batch_id: senderBatchId, email_subject: request.subject ?? 'M-FLOW authorized settlement' },
      items: [{ recipient_type: 'EMAIL', amount: { value: request.amount, currency: request.currency }, note: request.note ?? 'Authorized M-FLOW settlement', receiver: request.recipient }]
    };
    const res = await fetch(`${this.baseUrl()}/v1/payments/payouts`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new MFlowError('PAYPAL_PAYOUT_FAILED', `PayPal payout failed with HTTP ${res.status}`, 502);
    return { rail: this.name, externalReference: body.batch_header?.payout_batch_id ?? null, status: body.batch_header?.batch_status ?? null };
  }
  async status(reference) {
    if (!reference) throw new MFlowError('INVALID_REFERENCE', 'PayPal payout batch reference is required');
    const token = await this.#accessToken();
    const res = await fetch(`${this.baseUrl()}/v1/payments/payouts/${encodeURIComponent(reference)}`, { headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' } });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new MFlowError('PAYPAL_STATUS_FAILED', `PayPal status failed with HTTP ${res.status}`, 502);
    return body;
  }
}
