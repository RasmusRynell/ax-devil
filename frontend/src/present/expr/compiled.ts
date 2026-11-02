import type { CodePoints } from "./codePoints";

/** A compiled expression over one row: numbers, bools (0, 1), string ids, colors (0xRRGGBBAA) and NaN for null. */
export type RowFn = (row: number) => number;

/** A compiled text expression: appends the row's text to `out`, false when the text is null. */
export type Emitter = (row: number, out: CodePoints) => boolean;

/** A closure and, when it never varies, its value; constants fold and are read by layout without a call. */
export interface Scalar {
  readonly fn: RowFn;
  readonly constant: number | undefined;
}

/** What a length is relative to, known only at layout. */
export const Basis = {
  None: 0,
  BoxW: 1,
  BoxH: 2,
  BoxMin: 3,
  ImageW: 4,
  ImageH: 5,
  ImageMin: 6,
} as const;
export type BasisValue = (typeof Basis)[keyof typeof Basis];

/** `clamp(px + k * basis, lo, hi)` in screen pixels; the one form every length takes. */
export interface CLength {
  readonly px: Scalar;
  readonly k: Scalar;
  readonly lo: Scalar;
  readonly hi: Scalar;
  readonly basis: BasisValue;
  /** Whether lo or hi bounds it; clamped lengths cannot be added to anything. */
  readonly clamped: boolean;
}

/** An image-space rectangle as four closures (left, top, right, bottom); NaN when absent. */
export interface CBox {
  readonly l: RowFn;
  readonly t: RowFn;
  readonly r: RowFn;
  readonly b: RowFn;
}

/** The result of compiling an expression, by type. */
export type Compiled =
  | { readonly type: "number" | "bool" | "string" | "color"; readonly scalar: Scalar }
  | { readonly type: "text"; readonly emit: Emitter }
  | { readonly type: "colors"; readonly slot: number; readonly side: "self" | "source" | "target" }
  | { readonly type: "length"; readonly length: CLength }
  | { readonly type: "box"; readonly box: CBox }
  | { readonly type: "point"; readonly x: RowFn; readonly y: RowFn };

/** A constant. */
export function konst(value: number): Scalar {
  return { fn: () => value, constant: value };
}

/** A closure that varies per row. */
export function dynamic(fn: RowFn): Scalar {
  return { fn, constant: undefined };
}

const ARITHMETIC = {
  "+": (x: number, y: number) => x + y,
  "-": (x: number, y: number) => x - y,
  "*": (x: number, y: number) => x * y,
  // x / 0 is null, not infinity.
  "/": (x: number, y: number) => (y === 0 ? Number.NaN : x / y),
  min: (x: number, y: number) => Math.min(x, y),
  max: (x: number, y: number) => Math.max(x, y),
} as const;

/** The operators of `arith`. */
export type ArithOp = keyof typeof ARITHMETIC;

/** Combine two scalars; constants fold. */
export function arith(op: ArithOp, a: Scalar, b: Scalar): Scalar {
  const apply = ARITHMETIC[op];
  if (a.constant !== undefined && b.constant !== undefined)
    return konst(apply(a.constant, b.constant));
  const [x, y] = [a.fn, b.fn];
  switch (op) {
    case "+":
      return dynamic((row) => x(row) + y(row));
    case "-":
      return dynamic((row) => x(row) - y(row));
    case "*":
      return dynamic((row) => x(row) * y(row));
    case "/":
      return dynamic((row) => {
        const divisor = y(row);
        return divisor === 0 ? Number.NaN : x(row) / divisor;
      });
    case "min":
      return dynamic((row) => Math.min(x(row), y(row)));
    case "max":
      return dynamic((row) => Math.max(x(row), y(row)));
  }
}
