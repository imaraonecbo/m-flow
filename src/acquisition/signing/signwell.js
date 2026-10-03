import { fetchJson, requiredEnv } from '../net.js';

function markdownToHtml(md) {
  return `<!doctype html><html><body><pre style="white-space:pre-wrap;font-family:Arial,sans-serif">${String(md ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre></body></html>`;
}

export class SignWellAdapter {
  status() {
    return { enabled: Boolean(process.env.SIGNWELL_API_KEY?.trim()), requiresCredential: true, ready: Boolean(process.env.SIGNWELL_API_KEY?.trim()) };
  }

  async createSignatureRequest({ name, subject, message, markdown, recipients, testMode = false }) {
    const key = requiredEnv('SIGNWELL_API_KEY');
    if (!Array.isArray(recipients) || recipients.length < 2) throw new Error('SIGNATURE_RECIPIENTS_REQUIRED');
    if (recipients.some((r) => !r?.email || !r?.name || !r?.id)) throw new Error('SIGNATURE_RECIPIENT_INVALID');

    const payload = {
      test_mode: Boolean(testMode),
      files: [{ name: `${name || 'M-FLOW Agreement'}.html`, file_base64: Buffer.from(markdownToHtml(markdown)).toString('base64') }],
      name: name || 'M-FLOW Agreement',
      subject: subject || 'M-FLOW agreement signature request',
      message: message || 'Please review the attached M-FLOW transaction agreement and sign only if you are authorized to do so.',
      recipients,
      draft: false,
      with_signature_page: true,
      apply_signing_order: false,
      allow_decline: true,
      allow_reassign: false,
      reminders: true
    };

    return fetchJson('https://www.signwell.com/api/v1/documents', {
      method: 'POST',
      headers: { 'X-Api-Key': key, 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }
}
