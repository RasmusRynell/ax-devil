import { type CLength, type Scalar, arith, dynamic, konst } from "./compiled";

const UNBOUNDED_LOW = konst(-Infinity);
const UNBOUNDED_HIGH = konst(Infinity);

/** A length of `px` pixels. */
export function constantLength(px: number): CLength {
  return {
    px: konst(px),
    k: konst(0),
    lo: UNBOUNDED_LOW,
    hi: UNBOUNDED_HIGH,
    basis: 0,
    clamped: false,
  };
}

/** The pixels of a length that is a plain constant, else undefined. */
export function plainPixels(length: CLength): number | undefined {
  const plain = length.basis === 0 && !length.clamped && length.k.constant === 0;
  return plain ? length.px.constant : undefined;
}

/** `a + b` or `a - b`; the result is relative to the one basis involved. Returns an error message instead when not allowed. */
export function sum(a: CLength, b: CLength, op: "+" | "-"): CLength | string {
  if (a.clamped || b.clamped)
    return "a clamped length cannot be added to; apply min, max or clamp last";
  if (a.basis !== 0 && b.basis !== 0 && a.basis !== b.basis)
    return "a length is relative to only one of box.w, box.h, box.min, image.w, image.h, image.min";
  return { ...a, px: arith(op, a.px, b.px), k: arith(op, a.k, b.k), basis: a.basis || b.basis };
}

/** `a * n` or `a / n` for a number n; a negative n swaps the bounds. */
export function scale(a: CLength, n: Scalar, op: "*" | "/"): CLength {
  const px = arith(op, a.px, n);
  const k = arith(op, a.k, n);
  if (!a.clamped) return { ...a, px, k };
  const lo = arith(op, a.lo, n);
  const hi = arith(op, a.hi, n);
  if (n.constant !== undefined)
    return n.constant < 0 ? { ...a, px, k, lo: hi, hi: lo } : { ...a, px, k, lo, hi };
  return { ...a, px, k, lo: arith("min", lo, hi), hi: arith("max", lo, hi) };
}

/** `clamp(a, lo, hi)` with constant bounds; clamping a clamped length composes. */
export function bound(a: CLength, lo: number, hi: number): CLength {
  const fit = (edge: Scalar): Scalar =>
    edge.constant !== undefined
      ? konst(Math.min(Math.max(edge.constant, lo), hi))
      : dynamic((row) => Math.min(Math.max(edge.fn(row), lo), hi));
  return { ...a, lo: fit(a.lo), hi: fit(a.hi), clamped: true };
}
