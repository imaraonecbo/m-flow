import { MFlowError } from '../core/errors.js';

function requiredString(obj, key) {
  if (typeof obj[key] !== 'string' || obj[key].trim() === '') throw new MFlowError('INVALID_OPPORTUNITY', `${key} is required`);
}
function requiredPositiveDecimal(obj, key) {
  requiredString(obj, key);
  if (!/^(?!-)(?:\d+)(?:\.\d{1,6})?$/.test(obj[key])) throw new MFlowError('INVALID_OPPORTUNITY', `${key} must be a non-negative fixed-precision decimal string`);
}

export const opportunitySchema = {
  type: 'object',
  additionalProperties: false,
  required: ['volume', 'unitPrice', 'leadTimeDays', 'counterpartyId', 'marginThreshold', 'cogs', 'financingCost', 'platformFee', 'requiredCapital', 'durationDays', 'counterpartyScore', 'volatilityFactor'],
  properties: {
    volume: { type: 'string', pattern: '^\\d+(?:\\.\\d{1,6})?$' },
    unitPrice: { type: 'string' },
    leadTimeDays: { type: 'string' },
    counterpartyId: { type: 'string' },
    marginThreshold: { type: 'string' },
    cogs: { type: 'string' },
    financingCost: { type: 'string' },
    platformFee: { type: 'string' },
    requiredCapital: { type: 'string' },
    durationDays: { type: 'string' },
    counterpartyScore: { type: 'integer', minimum: 0, maximum: 10000 },
    volatilityFactor: { type: 'integer', minimum: 0, maximum: 10000 }
  }
};

export function validateOpportunity(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new MFlowError('INVALID_OPPORTUNITY', 'Opportunity must be a JSON object');
  const allowed = new Set(Object.keys(opportunitySchema.properties));
  for (const key of Object.keys(input)) if (!allowed.has(key)) throw new MFlowError('INVALID_OPPORTUNITY', `Unexpected property: ${key}`);
  for (const key of opportunitySchema.required) if (!(key in input)) throw new MFlowError('INVALID_OPPORTUNITY', `${key} is required`);
  ['volume','unitPrice','leadTimeDays','marginThreshold','cogs','financingCost','platformFee','requiredCapital','durationDays'].forEach((k) => requiredPositiveDecimal(input, k));
  requiredString(input, 'counterpartyId');
  for (const k of ['counterpartyScore','volatilityFactor']) {
    if (!Number.isInteger(input[k]) || input[k] < 0 || input[k] > 10000) throw new MFlowError('INVALID_OPPORTUNITY', `${k} must be an integer from 0 to 10000`);
  }
  return true;
}
