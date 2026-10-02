import { canonicalize } from '../core/canonical.js';
import { sha256Hex } from '../crypto/evidence.js';

export function buildTermsSheet(opportunity, score) {
  const now = new Date().toISOString();
  const body = [
    '# M-FLOW Transaction Terms Sheet', '',
    `Generated: ${now}`,
    `Opportunity ID: ${opportunity.id}`,
    `Counterparty ID: ${opportunity.counterpartyId}`,
    '',
    '## Commercial data',
    `Volume: ${opportunity.volume}`,
    `Unit price: ${opportunity.unitPrice}`,
    `Lead time (days): ${opportunity.leadTimeDays}`,
    `Required capital: ${opportunity.requiredCapital}`,
    `Duration (days): ${opportunity.durationDays}`,
    '',
    '## Calculated economics',
    `Gross revenue: ${score.grossRevenue}`,
    `Net profit: ${score.netProfit}`,
    `Capital velocity: ${score.capitalVelocity}`,
    `Risk-adjusted score (basis points): ${score.riskAdjustedScore}`,
    '',
    'This document reflects submitted transaction data and M-FLOW calculations. It is not a funding commitment.'
  ].join('\n');
  return { type: 'TERMS_SHEET', content: body, sha256: sha256Hex(body) };
}

export function buildCapitalDistributionContract(opportunity, score) {
  const object = {
    documentType: 'CAPITAL_DISTRIBUTION_CONTRACT',
    generatedAt: new Date().toISOString(),
    opportunityId: opportunity.id,
    requiredCapital: opportunity.requiredCapital,
    netProfit: score.netProfit,
    distributions: {
      principalReturn: opportunity.requiredCapital,
      financingCost: opportunity.financingCost,
      platformFee: opportunity.platformFee,
      residualProfit: score.netProfit
    },
    status: 'PROPOSED'
  };
  const canonical = canonicalize(object);
  return { type: object.documentType, content: JSON.stringify(object, null, 2), sha256: sha256Hex(canonical) };
}
