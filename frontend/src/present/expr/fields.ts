import {
  Basis,
  type BasisValue,
  type CBox,
  type CLength,
  type Compiled,
  type RowFn,
  dynamic,
  konst,
} from "./compiled";
import type { EvalContext } from "./context";
import type { Side } from "./ir";
import type { ExprType } from "./types";

/** One field of the sample an expression can name. */
export interface FieldDef {
  readonly type: ExprType;
  /** Lengths need the box or image on screen, which only layout knows. */
  readonly isLength?: boolean;
  /** Only relation scope has it. */
  readonly relationOnly?: boolean;
  lower(ctx: EvalContext, side: Side): Compiled;
}

/** A read of one entity, through the relation end `side` when not the row itself. */
export function through(ctx: EvalContext, side: Side, read: RowFn, missing: number): RowFn {
  if (side === "self") return read;
  const ends = side === "source" ? () => ctx.relSource : () => ctx.relTarget;
  return (relation) => {
    const entity = ends()[relation];
    return entity < 0 ? missing : read(entity);
  };
}

const scalar = (type: "number" | "string", missing: number, read: (ctx: EvalContext) => RowFn) => ({
  type,
  lower: (ctx: EvalContext, side: Side): Compiled => ({
    type,
    scalar: dynamic(through(ctx, side, read(ctx), missing)),
  }),
});

function boxOf(ctx: EvalContext, side: Side): CBox {
  const edge = (offset: number): RowFn =>
    through(ctx, side, (e) => ctx.box[4 * e + offset], Number.NaN);
  return { l: edge(0), t: edge(1), r: edge(2), b: edge(3) };
}

function lengthOf(basis: BasisValue): CLength {
  return {
    px: konst(0),
    k: konst(1),
    lo: konst(-Infinity),
    hi: konst(Infinity),
    basis,
    clamped: false,
  };
}

const length = (basis: BasisValue): FieldDef => ({
  type: "length",
  isLength: true,
  lower: () => ({ type: "length", length: lengthOf(basis) }),
});

/** Every field by its dotted name. `attr.<name>`, `source.` and `target.` are handled by the checker. */
export const FIELDS: Readonly<Record<string, FieldDef>> = {
  id: scalar("string", -1, (ctx) => (e) => ctx.entityId[e]),
  class: scalar("string", -1, (ctx) => (e) => ctx.classId[e]),
  score: scalar("number", Number.NaN, (ctx) => (e) => ctx.score[e]),
  confidence: scalar("number", Number.NaN, (ctx) => (e) => ctx.confidence[e]),
  // The motion states are the strings 0 to 2 of every sample (see `StringLookup`).
  motion_state: scalar("string", -1, (ctx) => (e) => ctx.motion[e] - 1),
  "velocity.x": scalar("number", Number.NaN, (ctx) => (e) => ctx.velocity[2 * e]),
  "velocity.y": scalar("number", Number.NaN, (ctx) => (e) => ctx.velocity[2 * e + 1]),
  box: { type: "box", lower: (ctx, side) => ({ type: "box", box: boxOf(ctx, side) }) },
  "box.w": length(Basis.BoxW),
  "box.h": length(Basis.BoxH),
  "box.min": length(Basis.BoxMin),
  "image.w": length(Basis.ImageW),
  "image.h": length(Basis.ImageH),
  "image.min": length(Basis.ImageMin),
  relation: { ...scalar("string", -1, (ctx) => (r) => ctx.relKind[r]), relationOnly: true },
};
