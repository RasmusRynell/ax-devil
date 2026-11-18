import { ANCHORS } from "./checkCall";
import { type Compiled, type Emitter, type RowFn, arith, dynamic } from "./compiled";
import { palette, ramp, top, withAlpha } from "./color";
import type { IR } from "./ir";
import type { Lowerer } from "./lower";
import { bound, plainPixels } from "./lowerLength";
import { chooseText, fixedText, selectText, trimText } from "./lowerText";
import { kindOf } from "./types";

type CallNode = Extract<IR, { op: "call" }>;
type Lower = (l: Lowerer, node: CallNode, target: string) => Compiled;
type Selectable = "number" | "bool" | "string" | "color";

function selectableTarget(l: Lowerer, node: IR, target: string): Selectable | "text" {
  if (
    target === "number" ||
    target === "bool" ||
    target === "string" ||
    target === "color" ||
    target === "text"
  )
    return target;
  return l.fail(node, `cannot choose between ${target} values`);
}

/** Wrap scalars of one type, or texts, for the callers that pick between values. */
function pick(
  l: Lowerer,
  node: IR,
  target: Selectable | "text",
  make: (scalar: (n: IR) => RowFn) => RowFn,
  makeText: (text: (n: IR) => Emitter) => Emitter,
): Compiled {
  if (target === "text") return { type: "text", emit: makeText((n) => l.emitter(n)) };
  return { type: target, scalar: dynamic(make((n) => l.scalar(n, target).fn)) };
}

const ifCall: Lower = (l, node, target) => {
  const kind = selectableTarget(l, node, target);
  const condition = l.scalar(node.args[0], "bool").fn;
  return pick(
    l,
    node,
    kind,
    (s) => {
      const [then, otherwise] = [s(node.args[1]), s(node.args[2])];
      return (row) => (condition(row) === 1 ? then(row) : otherwise(row));
    },
    (t) => chooseText(condition, t(node.args[1]), t(node.args[2])),
  );
};

const caseCall: Lower = (l, node, target) => {
  const kind = selectableTarget(l, node, target);
  const [subject, ...rest] = node.args;
  const type = kindOf(subject.ty) ?? "number";
  if (type !== "number" && type !== "bool" && type !== "string" && type !== "color")
    return l.fail(subject, "case compares strings, numbers, bools or colors");
  const value = l.scalar(subject, type).fn;
  const last = rest.length - 1;
  const keys = rest.filter((_, i) => i % 2 === 0 && i < last).map((key) => l.scalar(key, type).fn);
  const results = rest.filter((_, i) => i % 2 === 1);
  const fallback = rest[last];
  return pick(
    l,
    node,
    kind,
    (s) => {
      const picked = results.map((result) => s(result));
      const otherwise = s(fallback);
      return (row) => {
        const v = value(row);
        for (let i = 0; i < keys.length; i++) {
          const k = keys[i](row);
          if (v === k || (v !== v && k !== k)) return picked[i](row);
        }
        return otherwise(row);
      };
    },
    (t) => selectText(value, keys, results.map(t), t(fallback)),
  );
};

/** min, max and clamp over numbers, or over a length and constant pixel bounds. */
function bounded(l: Lowerer, node: CallNode, low: boolean, high: boolean): Compiled {
  const [first, ...limits] = node.args;
  if (kindOf(node.ty) !== "length") {
    const x = l.scalar(first, "number");
    const [lo, hi] = limits.map((limit) => l.scalar(limit, "number"));
    if (node.name === "clamp")
      return { type: "number", scalar: arith("min", arith("max", x, lo), hi) };
    return { type: "number", scalar: arith(node.name === "min" ? "min" : "max", x, lo) };
  }
  // min and max are commutative: the length is the argument that is not a plain pixel size.
  const lengths = node.args.map((arg) => l.length(arg));
  const index = node.name === "clamp" || plainPixels(lengths[1]) !== undefined ? 0 : 1;
  const pixels = lengths.filter((_, i) => i !== index).map(plainPixels);
  if (pixels.some((px) => px === undefined)) {
    return l.fail(node, `${node.name} of a length needs plain pixel bounds like 20px`, "length");
  }
  const [lo, hi] =
    node.name === "clamp"
      ? [pixels[0] as number, pixels[1] as number]
      : [low ? (pixels[0] as number) : -Infinity, high ? (pixels[0] as number) : Infinity];
  if (lo > hi) return l.fail(node, "the lower bound is above the upper bound", "length");
  return { type: "length", length: bound(lengths[index], lo, hi) };
}

const CALLS: Readonly<Record<string, Lower>> = {
  if: ifCall,
  case: caseCall,
  min: (l, node) => bounded(l, node, false, true),
  max: (l, node) => bounded(l, node, true, false),
  clamp: (l, node) => bounded(l, node, true, true),
  alpha: (l, node) => ({
    type: "color",
    scalar: withAlpha(l.scalar(node.args[0], "color"), l.scalar(node.args[1], "number")),
  }),
  ramp(l, node) {
    const [value, ...pairs] = node.args;
    const at = pairs.filter((_, i) => i % 2 === 0).map((stop) => l.constant(stop, "number"));
    const color = pairs.filter((_, i) => i % 2 === 1).map((stop) => l.constant(stop, "color"));
    for (let i = 1; i < at.length; i++) {
      if (!(at[i] > at[i - 1]))
        l.fail(pairs[2 * i], "ramp stops must be strictly increasing", "ramp");
    }
    return { type: "color", scalar: ramp(l.scalar(value, "number"), { at, color }) };
  },
  palette(l, node) {
    const list = node.args[1];
    if (list.op !== "list") return l.fail(list, "palette takes a list of colors", "constant");
    const colors = list.items.map((item) => l.constant(item, "color"));
    return { type: "color", scalar: palette(l.ctx, l.scalar(node.args[0], "string"), colors) };
  },
  top(l, node) {
    const colors = l.lower(node.args[0], "colors");
    if (colors.type !== "colors")
      return l.fail(node.args[0], `expected colors, found ${colors.type}`);
    return { type: "color", scalar: top(l.ctx, colors.slot, colors.side) };
  },
  inset(l, node) {
    const { l: left, t: topEdge, r: right, b: bottom } = l.box(node.args[0]);
    const f = l.scalar(node.args[1], "number").fn;
    // A fraction outside 0..0.5 would turn the box inside out: no box.
    const edge =
      (own: RowFn, other: RowFn, sign: 1 | -1): RowFn =>
      (row) => {
        const fraction = f(row);
        return fraction >= 0 && fraction <= 0.5
          ? own(row) + sign * fraction * (other(row) - own(row))
          : Number.NaN;
      };
    return {
      type: "box",
      box: {
        l: edge(left, right, 1),
        t: edge(topEdge, bottom, 1),
        r: edge(right, left, 1),
        b: edge(bottom, topEdge, 1),
      },
    };
  },
  anchor(l, node) {
    const { l: left, t: topEdge, r: right, b: bottom } = l.box(node.args[0]);
    const name = (node.args[1] as Extract<IR, { op: "str" }>).value;
    const [ax, ay] = ANCHORS[name];
    return {
      type: "point",
      x: (row) => left(row) + ax * (right(row) - left(row)),
      y: (row) => topEdge(row) + ay * (bottom(row) - topEdge(row)),
    };
  },
  trim(l, node) {
    const word = (index: number, fallback: string): string => {
      const arg = node.args[index];
      return arg ? (arg as Extract<IR, { op: "str" }>).value : fallback;
    };
    const count = l.scalar(node.args[1], "number").fn;
    return {
      type: "text",
      emit: trimText(l.emitter(node.args[0]), count, word(2, "end"), word(3, "…"), word(4, "")),
    };
  },
  fixed(l, node) {
    const places = (node.args[1] as Extract<IR, { op: "num" }>).value;
    return { type: "text", emit: fixedText(l.scalar(node.args[0], "number").fn, places) };
  },
};

/** Lower a function call. */
export function lowerCall(l: Lowerer, node: CallNode, target: string): Compiled {
  return CALLS[node.name](l, node, target);
}
