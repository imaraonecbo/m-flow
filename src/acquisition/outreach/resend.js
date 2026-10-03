import { fetchJson, requiredEnv } from '../net.js';

export class ResendOutreach {
  status() {
    return { enabled: Boolean(process.env.RESEND_API_KEY?.trim() && process.env.M_FLOW_OUTREACH_FROM?.trim()), requiresCredential: true, ready: Boolean(process.env.RESEND_API_KEY?.trim() && process.env.M_FLOW_OUTREACH_FROM?.trim()) };
  }

  async send({ to, subject, html, idempotencyKey }) {
    const key = requiredEnv('RESEND_API_KEY');
    const from = requiredEnv('M_FLOW_OUTREACH_FROM');
    if (!to || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)) throw new Error('OUTREACH_RECIPIENT_INVALID');
    const response = await fetchJson('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'content-type': 'application/json', ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}) },
      body: JSON.stringify({ from, to: [to], subject, html })
    });
    return response;
  }
}
