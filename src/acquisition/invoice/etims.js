import { fetchJson, requiredEnv, envPresent } from '../net.js';

export class ETimsVerifier {
  status() {
    return {
      enabled: Boolean(process.env.M_FLOW_ETIMS_VERIFY_URL?.trim() && process.env.M_FLOW_ETIMS_BEARER_TOKEN?.trim()),
      ...envPresent('M_FLOW_ETIMS_VERIFY_URL','M_FLOW_ETIMS_BEARER_TOKEN'),
      certifiedIntegrator: process.env.M_FLOW_ETIMS_CERTIFIED === 'true'
    };
  }

  async verify(payload) {
    if (process.env.M_FLOW_ETIMS_CERTIFIED !== 'true') throw new Error('ETIMS_INTEGRATION_NOT_CERTIFIED');
    const url = requiredEnv('M_FLOW_ETIMS_VERIFY_URL');
    const token = requiredEnv('M_FLOW_ETIMS_BEARER_TOKEN');
    return fetchJson(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }
}
