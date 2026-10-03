import crypto from 'node:crypto';
import { fetchJson, requiredEnv, envPresent } from '../net.js';

function signedHeaders({ method, path, body = '' }) {
  const ts = String(Math.floor(Date.now() / 1000));
  const token = requiredEnv('SUMSUB_APP_TOKEN');
  const secret = requiredEnv('SUMSUB_SECRET_KEY');
  const signature = crypto.createHmac('sha256', secret).update(ts + method.toUpperCase() + path + body).digest('hex');
  return { 'content-type': 'application/json', 'X-App-Token': token, 'X-App-Access-Sig': signature, 'X-App-Access-Ts': ts };
}

export class SumsubKYB {
  status() {
    return { ...envPresent('SUMSUB_APP_TOKEN','SUMSUB_SECRET_KEY','SUMSUB_KYB_LEVEL'), baseUrl: process.env.SUMSUB_BASE_URL ?? 'https://api.sumsub.com' };
  }
  async createCompany({ externalUserId, companyName, country, registrationNumber }) {
    const level = requiredEnv('SUMSUB_KYB_LEVEL');
    const payload = { externalUserId, fixedInfo: { companyInfo: { companyName, registrationNumber, country } }, type: 'company' };
    const body = JSON.stringify(payload);
    const path = `/resources/applicants?levelName=${encodeURIComponent(level)}`;
    return fetchJson(`${process.env.SUMSUB_BASE_URL ?? 'https://api.sumsub.com'}${path}`, { method:'POST', headers:signedHeaders({method:'POST',path,body}), body });
  }
  async statusByApplicantId(applicantId) {
    const path = `/resources/applicants/${encodeURIComponent(applicantId)}/status`;
    return fetchJson(`${process.env.SUMSUB_BASE_URL ?? 'https://api.sumsub.com'}${path}`, { method:'GET', headers:signedHeaders({method:'GET',path}) });
  }
  async statusByExternalUserId(externalUserId) {
    const path = `/resources/applicants/-;externalUserId=${encodeURIComponent(externalUserId)}/one`;
    return fetchJson(`${process.env.SUMSUB_BASE_URL ?? 'https://api.sumsub.com'}${path}`, { method:'GET', headers:signedHeaders({method:'GET',path}) });
  }
}
