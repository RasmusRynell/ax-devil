/**
 * What expression evaluation reads of one sample. This is the subset of the sample columns (spec section 4) that
 * `columns.ts` implements, declared here so the language depends on no codec; `SampleView` of `columns.ts` is
 * assignable to it (checked in `expr.test.ts`).
 */

import type { CodePoints } from "./codePoints";

/** Where an attribute column keeps its values; the kinds are the wire's. */
export const AttrKind = { Bool: 0, Number: 1, String: 2, Colors: 3 } as const;
export type AttrKindValue = (typeof AttrKind)[keyof typeof AttrKind];

/**
 * The strings of one sample, compared, decoded and hashed without making JS strings or views. Each distinct string
 * has one id in [0, count), so a literal has the same id in every field. Ids 0, 1 and 2 are the motion states
 * `moving`, `stationary` and `unknown`: the string id of a `motion` value `m` above 0 is `m - 1`.
 */
export interface StringLookup {
  readonly count: number;
  equals(id: number, utf8: Uint8Array): boolean;
  /** Append the code points of string `id` to `out`. */
  decode(id: number, out: CodePoints): void;
  /** The CRC-32 (IEEE) of the UTF-8 bytes of string `id`, unsigned. */
  crc32(id: number): number;
}

/** One attribute of the primary classification, as a column over the rows. */
export interface AttrColumnView {
  /** String id of the attribute name. */
  readonly name: number;
  readonly kind: AttrKindValue;
  /** Per row: 0, 1 or 2 for missing. */
  readonly bool?: Uint8Array;
  /** Per row; NaN for missing. */
  readonly number?: Float64Array;
  /** Per row: a string id, or -1 for missing. */
  readonly id?: Int32Array;
  /** Per row, two numbers: the first entry in the shared color list (-1 for no value) and the count. */
  readonly span?: Int32Array;
}

/** The columns of one sample that expressions read. Colors are packed 0xRRGGBBAA. */
export interface SampleView {
  readonly rows: number;
  readonly relationCount: number;
  readonly entityId: Int32Array;
  /** Four numbers per row: left, top, right, bottom in image fractions. */
  readonly box: Float32Array;
  /** 0 none, 1 moving, 2 stationary, 3 unknown. */
  readonly motion: Uint8Array;
  /** Two numbers per row; NaN for missing. */
  readonly velocity: Float32Array;
  /** String id of the primary class, -1 for none. */
  readonly classId: Int32Array;
  readonly score: Float64Array;
  readonly confidence: Float64Array;
  /** Words per row of `classMask`. */
  readonly maskWords: number;
  /** `maskWords` words per row: bit `i % 32` of word `i / 32` is filter option `i`. */
  readonly classMask: Uint32Array;
  readonly attrs: readonly AttrColumnView[];
  readonly colors: { readonly rgba: Uint32Array; readonly score: ArrayLike<number> };
  readonly relKind: Int32Array;
  /** Entity row of each end, -1 when the end has no row. */
  readonly relSource: Int32Array;
  readonly relTarget: Int32Array;
  readonly strings: StringLookup;
}
