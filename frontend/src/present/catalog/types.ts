import type { CLength, Compiled, Emitter, Scalar } from "../expr/compiled";
import type { EvalContext } from "../expr/context";

/** The closed set of feature toggles; a lane hides a layer by hiding its feature. */
export const FEATURES = [
  "outlines",
  "ids",
  "class_names",
  "score",
  "confidence",
  "speed",
  "movement",
  "attributes",
  "relations",
] as const;
export type Feature = (typeof FEATURES)[number];

/** Every mark a layer can hold, in the order the evaluator numbers them. */
export const MARK_TYPES = [
  "box",
  "circle",
  "line",
  "arrow",
  "polygon",
  "bar",
  "text",
  "label",
] as const;
export type MarkName = (typeof MARK_TYPES)[number];

/** Label and text weights. */
export const WEIGHTS = ["regular", "medium", "semibold", "bold"] as const;
export type WeightName = (typeof WEIGHTS)[number];

/** The expression-valued fields of marks. */
export type FieldName =
  | "when"
  | "on"
  | "w"
  | "h"
  | "dx"
  | "dy"
  | "outset"
  | "min_size"
  | "stroke"
  | "fill"
  | "width"
  | "dash"
  | "radius"
  | "r"
  | "from"
  | "to"
  | "gain"
  | "max"
  | "head"
  | "head_width"
  | "min_speed"
  | "vx"
  | "vy"
  | "value"
  | "text"
  | "color"
  | "size"
  | "background"
  | "gap";

/** One placement candidate of a label: the point `at` on the reference rect, plus an offset, takes the label's `anchor`. */
export interface PlaceSpec {
  /** Null: always a candidate. */
  readonly when: Scalar | null;
  readonly at: readonly [number, number];
  readonly anchor: readonly [number, number];
  readonly dx: CLength;
  readonly dy: CLength;
}

/** One run of a label: text, or a bar filled to a value. */
export interface RunSpec {
  readonly text: Emitter | null;
  /** The bar's fill from 0 to 1; null for a text run. */
  readonly bar: Scalar | null;
  readonly color: Scalar;
  readonly weight: WeightName;
}

/** A mark with every expression compiled. Absent fields use the mark's defaults. */
export interface MarkSpec {
  readonly type: MarkName;
  readonly layer: number;
  /** Position among all marks of the catalog, for counting skipped marks. */
  readonly index: number;
  readonly pointer: string;
  readonly fields: Readonly<Partial<Record<FieldName, Compiled>>>;
  /** Top, right, bottom, left. */
  readonly margin: readonly CLength[] | null;
  /** Horizontal and vertical. */
  readonly pad: readonly CLength[] | null;
  readonly pin: readonly [number, number] | null;
  readonly font: string | null;
  readonly place: readonly PlaceSpec[];
  readonly runs: readonly RunSpec[];
}

/** An ordered group of marks drawn for each entity, or each relation. */
export interface LayerSpec {
  readonly index: number;
  readonly feature: Feature | null;
  readonly relations: boolean;
  readonly when: Scalar | null;
  readonly marks: readonly MarkSpec[];
}

/** Writes one variable's value for a row into the registers. */
export type Setter = (row: number) => void;

/** A variable: how to compute it for a row. The last matching rule wins, else the default (of the theme, if any). */
export interface VarProgram {
  readonly name: string;
  /** The default by theme index; 0 is the catalog's own. */
  readonly defaults: readonly Setter[];
  readonly assignments: readonly { readonly rule: number; readonly set: Setter }[];
}

/** A catalog ready to evaluate: no expression is parsed again. */
export interface CompiledCatalog {
  /** Unique per compile, so caches keyed by it never confuse two compiles of one id. */
  readonly uid: number;
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly origin: string | null;
  /** Bit i set when a layer has `FEATURES[i]`. */
  readonly features: number;
  /** Theme names; theme index 0 is no theme, theme i is `themes[i - 1]`. */
  readonly themes: readonly string[];
  readonly ctx: EvalContext;
  /** One condition per rule, in order. */
  readonly rules: readonly Scalar[];
  /** Variables in dependency order. */
  readonly vars: readonly VarProgram[];
  readonly layers: readonly LayerSpec[];
}
