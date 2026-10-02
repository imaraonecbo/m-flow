import { FixedDecimal } from '../finance/decimal.js';

export function matchProviders(providers, { amount, currency, region }) {
  const requested = FixedDecimal.from(amount);
  return providers.filter((p) => {
    if (!p.active || !p.verified) return false;
    if (!Array.isArray(p.currencies) || !p.currencies.includes(currency)) return false;
    if (region && Array.isArray(p.regions) && p.regions.length && !p.regions.includes(region)) return false;
    if (p.minCapital != null && requested.lt(p.minCapital)) return false;
    if (p.maxCapital != null && requested.gt(p.maxCapital)) return false;
    return true;
  }).sort((a, b) => {
    const ar = a.maxCapital == null ? null : FixedDecimal.from(a.maxCapital).sub(requested).abs();
    const br = b.maxCapital == null ? null : FixedDecimal.from(b.maxCapital).sub(requested).abs();
    if (!ar && !br) return 0;
    if (!ar) return 1;
    if (!br) return -1;
    return ar.lt(br) ? -1 : ar.gt(br) ? 1 : 0;
  });
}
