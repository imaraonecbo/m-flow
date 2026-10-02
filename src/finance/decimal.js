import { MFlowError } from '../core/errors.js';

const SCALE = 6n;
const UNITS = 10n ** SCALE;

function parseScaled(input) {
  if (typeof input !== 'string' && typeof input !== 'number' && typeof input !== 'bigint') {
    throw new MFlowError('INVALID_DECIMAL', 'Decimal must be a string, number, or bigint');
  }
  const s = String(input).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(s)) throw new MFlowError('INVALID_DECIMAL', `Invalid decimal: ${s}`);
  const negative = s.startsWith('-');
  const clean = negative ? s.slice(1) : s;
  const [whole, frac = ''] = clean.split('.');
  if (frac.length > Number(SCALE)) throw new MFlowError('PRECISION_EXCEEDED', `Maximum precision is ${SCALE} decimals`);
  const scaled = BigInt(whole) * UNITS + BigInt((frac + '0'.repeat(Number(SCALE))).slice(0, Number(SCALE)) || 0);
  return negative ? -scaled : scaled;
}

function formatScaled(scaled) {
  const negative = scaled < 0n;
  const abs = negative ? -scaled : scaled;
  const whole = abs / UNITS;
  const frac = String(abs % UNITS).padStart(Number(SCALE), '0');
  return `${negative ? '-' : ''}${whole}.${frac}`;
}

function divRound(num, den) {
  if (den === 0n) throw new MFlowError('DIVISION_BY_ZERO', 'Division by zero');
  const sign = (num < 0n) !== (den < 0n) ? -1n : 1n;
  const a = num < 0n ? -num : num;
  const b = den < 0n ? -den : den;
  const q = a / b;
  const r = a % b;
  const rounded = r * 2n >= b ? q + 1n : q;
  return sign * rounded;
}

export class FixedDecimal {
  constructor(scaled) { this.scaled = BigInt(scaled); }
  static from(value) { return value instanceof FixedDecimal ? value : new FixedDecimal(parseScaled(value)); }
  add(other) { return new FixedDecimal(this.scaled + FixedDecimal.from(other).scaled); }
  sub(other) { return new FixedDecimal(this.scaled - FixedDecimal.from(other).scaled); }
  mul(other) { return new FixedDecimal(divRound(this.scaled * FixedDecimal.from(other).scaled, UNITS)); }
  div(other) { return new FixedDecimal(divRound(this.scaled * UNITS, FixedDecimal.from(other).scaled)); }
  abs() { return new FixedDecimal(this.scaled < 0n ? -this.scaled : this.scaled); }
  max(other) { const o = FixedDecimal.from(other); return this.scaled >= o.scaled ? this : o; }
  min(other) { const o = FixedDecimal.from(other); return this.scaled <= o.scaled ? this : o; }
  lt(other) { return this.scaled < FixedDecimal.from(other).scaled; }
  lte(other) { return this.scaled <= FixedDecimal.from(other).scaled; }
  gt(other) { return this.scaled > FixedDecimal.from(other).scaled; }
  gte(other) { return this.scaled >= FixedDecimal.from(other).scaled; }
  eq(other) { return this.scaled === FixedDecimal.from(other).scaled; }
  toString() { return formatScaled(this.scaled); }
}

export class Money extends FixedDecimal {
  static from(value) { return value instanceof Money ? value : new Money(parseScaled(value)); }
}

export function clamp(value, low, high) {
  return FixedDecimal.from(value).max(low).min(high);
}

export const ZERO = FixedDecimal.from('0');
