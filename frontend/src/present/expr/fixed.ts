import type { CodePoints } from "./codePoints";

/** 10^0 .. 10^22: every power of ten a double holds exactly. */
const POW10 = [
  1, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16, 1e17,
  1e18, 1e19, 1e20, 1e21, 1e22,
];
const TWO_52 = 4503599627370496;
const SPLIT = 134217729; // 2^27 + 1, Dekker's splitter.
const BASE = 1e9;

/** Decimal digits of the rounded value, most significant first, in `digits[0..count)`. */
const digits = new Uint8Array(640);
/** The slow path's integer, as limbs of 9 decimal digits, least significant first. */
const limbs = new Float64Array(64);
const bits = new Float64Array(1);
const words = new Uint32Array(bits.buffer);

/** Write the decimal digits of the whole number `n` (below 2^53); returns how many. */
function wholeDigits(n: number): number {
  let count = 0;
  let rest = n;
  do {
    digits[count++] = rest % 10;
    rest = (rest - (rest % 10)) / 10;
  } while (rest > 0);
  for (let i = 0, j = count - 1; i < j; i++, j--) {
    const swap = digits[i];
    digits[i] = digits[j];
    digits[j] = swap;
  }
  return count;
}

/** round(value * 10^places) to nearest, ties to even, when the product is exact enough to split into two doubles. */
function scaledRound(value: number, places: number): number {
  const power = POW10[places];
  const product = value * power;
  // The error of the product, exactly (Dekker's two-product).
  const a = SPLIT * value;
  const aHigh = a - (a - value);
  const aLow = value - aHigh;
  const b = SPLIT * power;
  const bHigh = b - (b - power);
  const bLow = power - bHigh;
  const error = aHigh * bHigh - product + aHigh * bLow + aLow * bHigh + aLow * bLow;
  const whole = Math.floor(product);
  const fraction = product - whole;
  if (fraction > 0.5 || (fraction === 0.5 && (error > 0 || (error === 0 && whole % 2 === 1))))
    return whole + 1;
  return whole;
}

function multiply(count: number, factor: number): number {
  let carry = 0;
  for (let i = 0; i < count; i++) {
    const product = limbs[i] * factor + carry;
    carry = Math.floor(product / BASE);
    limbs[i] = product - carry * BASE;
  }
  if (carry > 0) limbs[count++] = carry;
  return count;
}

/** Halve the number in `limbs`; returns the bit that fell off. */
function halve(count: number): number {
  let carry = 0;
  for (let i = count - 1; i >= 0; i--) {
    const current = limbs[i] + carry * BASE;
    limbs[i] = Math.floor(current / 2);
    carry = current % 2;
  }
  return carry;
}

/** Digits of round(value * 10^places), exactly, for values the fast path cannot hold. Returns the digit count. */
function exactDigits(value: number, places: number): number {
  bits[0] = value;
  const exponentField = (words[1] >>> 20) & 0x7ff;
  const mantissa = (words[1] & 0xfffff) * 4294967296 + words[0] + (exponentField > 0 ? TWO_52 : 0);
  const exponent = Math.max(exponentField, 1) - 1075;
  limbs[0] = mantissa % BASE;
  limbs[1] = Math.floor(mantissa / BASE);
  let count = 2;
  for (let left = places; left > 0; left -= 9) count = multiply(count, 10 ** Math.min(left, 9));
  if (exponent > 0) {
    for (let left = exponent; left > 0; left -= 20)
      count = multiply(count, 2 ** Math.min(left, 20));
  } else {
    let sticky = 0;
    let half = 0;
    for (let i = 0; i < -exponent; i++) {
      sticky |= half;
      half = halve(count);
    }
    if (half === 1 && (sticky === 1 || limbs[0] % 2 === 1)) {
      limbs[count] = 0;
      let i = 0;
      while (limbs[i] === BASE - 1) limbs[i++] = 0;
      limbs[i] += 1;
      count = Math.max(count, i + 1);
    }
  }
  while (count > 1 && limbs[count - 1] === 0) count--;
  let length = 0;
  for (let i = count - 1; i >= 0; i--) {
    let limb = limbs[i];
    const start = length;
    for (let d = 0; d < 9; d++) {
      digits[start + 8 - d] = limb % 10;
      limb = Math.floor(limb / 10);
    }
    length += 9;
  }
  let lead = 0;
  while (lead < length - 1 && digits[lead] === 0) lead++;
  digits.copyWithin(0, lead, length);
  return length - lead;
}

/**
 * Append `value` with `places` decimals, rounded half to even on its exact binary value (what Rust `{:.2}` and
 * Python `f"{:.2f}"` print: 0.125 is "0.12", 0.375 is "0.38"), without making a string. False when the value is not a
 * finite number.
 */
export function emitFixed(value: number, places: number, out: CodePoints): boolean {
  if (!Number.isFinite(value)) return false;
  const negative = value < 0 || Object.is(value, -0);
  const magnitude = Math.abs(value);
  const count =
    places <= 22 && magnitude * POW10[places] < TWO_52
      ? wholeDigits(scaledRound(magnitude, places))
      : exactDigits(magnitude, places);
  if (negative) out.push(45);
  // At least one digit before the point: pad with zeros on the left.
  const whole = Math.max(count - places, 1);
  const padding = places + whole - count;
  for (let i = 0; i < places + whole; i++) {
    if (i === whole) out.push(46);
    out.push(48 + (i < padding ? 0 : digits[i - padding]));
  }
  return true;
}
