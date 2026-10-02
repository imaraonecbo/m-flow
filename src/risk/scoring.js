import { FixedDecimal, Money, clamp } from '../finance/decimal.js';
import { MFlowError } from '../core/errors.js';

export function calculateFinancials(input) {
  const volume = FixedDecimal.from(input.volume);
  const unitPrice = Money.from(input.unitPrice);
  const grossRevenue = new Money(volume.mul(unitPrice).scaled);
  const cogs = Money.from(input.cogs);
  const financingCost = Money.from(input.financingCost);
  const platformFee = Money.from(input.platformFee);
  const netProfit = grossRevenue.sub(cogs).sub(financingCost).sub(platformFee);
  const requiredCapital = Money.from(input.requiredCapital);
  const durationDays = FixedDecimal.from(input.durationDays);
  if (requiredCapital.lte('0') || durationDays.lte('0')) throw new MFlowError('INVALID_FINANCIALS', 'requiredCapital and durationDays must be greater than zero');
  const capitalVelocity = new FixedDecimal(netProfit.scaled).div(requiredCapital).div(durationDays);
  if (grossRevenue.lte('0')) throw new MFlowError('INVALID_FINANCIALS', 'grossRevenue must be greater than zero');
  const netMargin = new FixedDecimal(netProfit.scaled).div(grossRevenue);
  const marginThreshold = FixedDecimal.from(input.marginThreshold);
  return { grossRevenue: grossRevenue.toString(), netProfit: netProfit.toString(), capitalVelocity: capitalVelocity.toString(), netMargin: netMargin.toString(), marginThreshold: marginThreshold.toString(), marginPass: netMargin.gte(marginThreshold) }; 
}

export function scoreOpportunity(input, velocityTargetPerDay = '0.01') {
  const financials = calculateFinancials(input);
  const velocity = FixedDecimal.from(financials.capitalVelocity);
  const target = FixedDecimal.from(velocityTargetPerDay);
  if (target.lte('0')) throw new MFlowError('INVALID_SCORING_TARGET', 'Velocity scoring target must be greater than zero');
  const velocityScoreDecimal = clamp(velocity.div(target).mul('1'), '0', '1');
  const velocityScoreBps = Number((velocityScoreDecimal.scaled * 10000n) / 1000000n);
  const counterpartyScore = input.counterpartyScore;
  const volatilityFactor = input.volatilityFactor;
  // Exact integer implementation of: (Velocity*0.4)+(Counterparty*0.4)-(Volatility*0.2)
  const riskAdjustedScore = Math.trunc((velocityScoreBps * 4 + counterpartyScore * 4 - volatilityFactor * 2) / 10);
  return { ...financials, velocityScoreBps, counterpartyScore, volatilityFactor, riskAdjustedScore };
}
