import { FEATURES, MARK_TYPES, WEIGHTS } from "../catalog/types";
import type { CompiledCatalog, FieldName, LayerSpec, MarkSpec } from "../catalog/types";
import type { BasisValue, CBox, CLength, Compiled, Emitter, RowFn, Scalar } from "../expr/compiled";
import { konst } from "../expr/compiled";

/** The numbers of `MarkType`: the order of `MARK_TYPES`. */
export const Kind = {
  Box: 0,
  Circle: 1,
  Line: 2,
  Arrow: 3,
  Polygon: 4,
  Bar: 5,
  Text: 6,
  Label: 7,
} as const;
export type KindValue = (typeof Kind)[keyof typeof Kind];

/** The lengths a mark can have, as indexes into `MarkPlan.len`. */
export const Len = {
  W: 0,
  H: 1,
  Dx: 2,
  Dy: 3,
  Outset: 4,
  MinSize: 5,
  Width: 6,
  Radius: 7,
  R: 8,
  Max: 9,
  Head: 10,
  HeadWidth: 11,
  Size: 12,
  Gap: 13,
  MarginTop: 14,
  MarginRight: 15,
  MarginBottom: 16,
  MarginLeft: 17,
  PadX: 18,
  PadY: 19,
  /** Plain numbers, not lengths: a zoom never scales them. */
  Gain: 20,
  MinSpeed: 21,
} as const;
export const LEN_COUNT = 22;

/** Where each named field lands, and what it is when the mark does not write it (in px). */
const LENGTH_FIELDS: Readonly<Partial<Record<FieldName, readonly [number, number]>>> = {
  w: [Len.W, 0],
  h: [Len.H, 0],
  dx: [Len.Dx, 0],
  dy: [Len.Dy, 0],
  outset: [Len.Outset, 0],
  min_size: [Len.MinSize, 0],
  width: [Len.Width, 1],
  radius: [Len.Radius, 0],
  r: [Len.R, 0],
  max: [Len.Max, 100],
  head: [Len.Head, 6],
  head_width: [Len.HeadWidth, 3.5],
  size: [Len.Size, 0],
  gap: [Len.Gap, 0],
};

/**
 * One length ready for layout: `clamp(px + k * basis, lo, hi)`. Its four parts are constants unless a variable or
 * the sample feeds them, in which case `evaluate` writes them to `nums` at `offset` of the mark's block.
 */
export interface LengthPlan {
  /** Whether the mark wrote it; an absent length is its default and `px`. */
  readonly present: boolean;
  readonly basis: BasisValue;
  /** Lengths divide by the UI zoom; plain numbers do not. */
  readonly zoomed: boolean;
  /** Offset of the four numbers in the mark's block, or -1 when all four are constant. */
  readonly offset: number;
  readonly px: number;
  readonly k: number;
  readonly lo: number;
  readonly hi: number;
  /** For a constant length with no basis, its value before the zoom: `clamp(px, lo, hi)`; else NaN. */
  readonly fixed: number;
  readonly parts: readonly [Scalar, Scalar, Scalar, Scalar] | null;
}

/** One candidate position of a label or text. */
export interface PlacePlan {
  readonly when: Scalar | null;
  readonly atX: number;
  readonly atY: number;
  readonly anchorX: number;
  readonly anchorY: number;
  /** The first line's baseline, not a box edge, is on the point. */
  readonly baseline: boolean;
  readonly dx: LengthPlan;
  readonly dy: LengthPlan;
}

/** One run of a label. */
export interface RunPlan {
  readonly text: Emitter | null;
  readonly bar: Scalar | null;
  readonly color: Scalar;
  /** Index into `WEIGHTS`. */
  readonly weight: number;
}

/** A mark with everything `evaluate` and `layout` read, resolved once per catalog. */
export interface MarkPlan {
  readonly spec: MarkSpec;
  readonly kind: KindValue;
  readonly index: number;
  readonly layer: number;
  /** Null when the mark has no condition of its own. */
  readonly when: Scalar | null;
  /** A condition that is constant false: the mark is never drawn. */
  readonly never: boolean;
  /** Null: the row's own box. */
  readonly on: CBox | null;
  readonly pin: readonly [number, number] | null;
  readonly stroke: Scalar | null;
  readonly fill: Scalar | null;
  readonly dash: Scalar | null;
  readonly color: Scalar | null;
  readonly background: Scalar | null;
  readonly value: Scalar | null;
  readonly vx: Scalar | null;
  readonly vy: Scalar | null;
  readonly from: { readonly x: RowFn; readonly y: RowFn } | null;
  readonly to: { readonly x: RowFn; readonly y: RowFn } | null;
  /** The text of a `text` mark. */
  readonly text: Emitter | null;
  readonly runs: readonly RunPlan[];
  readonly place: readonly PlacePlan[];
  readonly len: readonly LengthPlan[];
  /** Every length with a dynamic part, in the order `evaluate` writes them. */
  readonly dynamic: readonly LengthPlan[];
  /** Numbers the mark writes to `nums`. */
  readonly numWords: number;
  /** The font family; empty for the app's default. */
  readonly family: string;
}

/** A layer ready to run. */
export interface LayerPlan {
  readonly index: number;
  readonly relations: boolean;
  /** Bit of the layer's feature in `EvalStyle.hidden`, 0 for none. */
  readonly featureBit: number;
  readonly when: Scalar | null;
  readonly never: boolean;
  readonly marks: readonly MarkPlan[];
}

/** The plans of a whole catalog. */
export interface Program {
  readonly layers: readonly LayerPlan[];
  readonly marks: readonly MarkPlan[];
}

const scalarOf = (c: Compiled | undefined): Scalar | null => (c && "scalar" in c ? c.scalar : null);
const boxOf = (c: Compiled | undefined): CBox | null => (c?.type === "box" ? c.box : null);
const lengthOf = (c: Compiled | undefined): CLength | null =>
  c?.type === "length" ? c.length : null;

class Builder {
  numWords = 0;
  readonly dynamic: LengthPlan[] = [];

  length(source: CLength | null, fallback: number, zoomed = true): LengthPlan {
    if (!source)
      return {
        present: false,
        basis: 0,
        zoomed,
        offset: -1,
        px: fallback,
        k: 0,
        lo: -Infinity,
        hi: Infinity,
        fixed: fallback,
        parts: null,
      };
    const parts = [source.px, source.k, source.lo, source.hi] as const;
    const constant = parts.every((part) => part.constant !== undefined);
    const plan: LengthPlan = {
      present: true,
      basis: source.basis,
      zoomed,
      offset: constant ? -1 : this.numWords,
      px: parts[0].constant ?? Number.NaN,
      k: parts[1].constant ?? Number.NaN,
      lo: parts[2].constant ?? Number.NaN,
      hi: parts[3].constant ?? Number.NaN,
      fixed:
        constant && source.basis === 0
          ? Math.min(
              Math.max(parts[0].constant as number, parts[2].constant as number),
              parts[3].constant as number,
            )
          : Number.NaN,
      parts: constant ? null : parts,
    };
    if (!constant) {
      this.numWords += 4;
      this.dynamic.push(plan);
    }
    return plan;
  }

  /** A plain number (no unit, no basis) as a length that no zoom scales. */
  number(scalar: Scalar | null, fallback: number): LengthPlan {
    if (!scalar) return this.length(null, fallback, false);
    return this.length(
      {
        px: scalar,
        k: konst(0),
        lo: konst(-Infinity),
        hi: konst(Infinity),
        basis: 0,
        clamped: false,
      },
      fallback,
      false,
    );
  }
}

function planMark(spec: MarkSpec): MarkPlan {
  const b = new Builder();
  const field = (name: FieldName): Compiled | undefined => spec.fields[name];
  const len: LengthPlan[] = Array.from({ length: LEN_COUNT }, () => b.length(null, 0));
  for (const [name, target] of Object.entries(LENGTH_FIELDS) as [FieldName, [number, number]][])
    len[target[0]] = b.length(lengthOf(field(name)), target[1]);
  (spec.margin ?? []).forEach((margin, side) => {
    len[Len.MarginTop + side] = b.length(margin, 0);
  });
  (spec.pad ?? []).forEach((pad, axis) => {
    len[Len.PadX + axis] = b.length(pad, 0);
  });
  len[Len.Gain] = b.number(scalarOf(field("gain")), 0.5);
  len[Len.MinSpeed] = b.number(scalarOf(field("min_speed")), 0.001);
  const place = spec.place.map((entry): PlacePlan => ({
    when: entry.when,
    atX: entry.at[0],
    atY: entry.at[1],
    anchorX: entry.anchor[0],
    anchorY: entry.anchor[1],
    baseline: entry.baseline,
    dx: b.length(entry.dx, 0),
    dy: b.length(entry.dy, 0),
  }));
  const point = (c: Compiled | undefined) => (c?.type === "point" ? { x: c.x, y: c.y } : null);
  const when = scalarOf(field("when"));
  const textField = field("text");
  const circle = spec.type === "circle";
  return {
    spec,
    kind: MARK_TYPES.indexOf(spec.type) as KindValue,
    index: spec.index,
    layer: spec.layer,
    when: when && when.constant === undefined ? when : null,
    never: when !== null && when.constant !== undefined && when.constant !== 1,
    on: boxOf(field("on")),
    // A circle sits in the middle of its reference rect unless it is pinned elsewhere.
    pin: spec.pin ?? (circle ? [0.5, 0.5] : null),
    stroke: scalarOf(field("stroke")),
    fill: scalarOf(field("fill")),
    dash: scalarOf(field("dash")),
    color: scalarOf(field("color")),
    background: scalarOf(field("background")),
    value: scalarOf(field("value")),
    vx: scalarOf(field("vx")),
    vy: scalarOf(field("vy")),
    from: point(field("from")),
    to: point(field("to")),
    text: textField?.type === "text" ? textField.emit : null,
    runs: spec.runs.map((run) => ({
      text: run.text,
      bar: run.bar,
      color: run.color,
      weight: WEIGHTS.indexOf(run.weight),
    })),
    place,
    len,
    dynamic: b.dynamic,
    numWords: b.numWords,
    family: spec.font ?? "",
  };
}

function planLayer(spec: LayerSpec): LayerPlan {
  const when = spec.when;
  return {
    index: spec.index,
    relations: spec.relations,
    featureBit: spec.feature === null ? 0 : 1 << FEATURES.indexOf(spec.feature),
    when: when && when.constant === undefined ? when : null,
    never: when !== null && when.constant !== undefined && when.constant !== 1,
    marks: spec.marks.map(planMark),
  };
}

const programs = new WeakMap<CompiledCatalog, Program>();

/** The plans of `catalog`, made on first use. */
export function programOf(catalog: CompiledCatalog): Program {
  let program = programs.get(catalog);
  if (!program) {
    const layers = catalog.layers.map(planLayer);
    const marks: MarkPlan[] = [];
    for (const layer of layers) for (const mark of layer.marks) marks[mark.index] = mark;
    program = { layers, marks };
    programs.set(catalog, program);
  }
  return program;
}
