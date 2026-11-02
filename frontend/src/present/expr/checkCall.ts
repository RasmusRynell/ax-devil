import type { Checker } from "./check";
import type { Span } from "./diagnostic";
import type { IR } from "./ir";
import { type Ty, kindOf } from "./types";

/** The anchors of a rectangle, as horizontal and vertical fractions. */
export const ANCHORS: Readonly<Record<string, readonly [number, number]>> = {
  "top-left": [0, 0],
  "top-center": [0.5, 0],
  "top-right": [1, 0],
  "center-left": [0, 0.5],
  center: [0.5, 0.5],
  "center-right": [1, 0.5],
  "bottom-left": [0, 1],
  "bottom-center": [0.5, 1],
  "bottom-right": [1, 1],
};

type Check = (c: Checker, args: readonly IR[], at: Span) => Ty;

function arity(
  c: Checker,
  name: string,
  args: readonly IR[],
  min: number,
  max: number,
  at: Span,
): void {
  if (args.length >= min && args.length <= max) return;
  const wanted = min === max ? `${min}` : `${min} to ${max}`;
  c.unit.diag.fail("arity", c.src, at, `${name} takes ${wanted} arguments, found ${args.length}`);
}

function literal(c: Checker, arg: IR, op: "str" | "num"): string | number {
  if (arg.op !== op || (arg.op === "num" && arg.px))
    c.fail(
      arg.span,
      `expected ${op === "str" ? "a quoted text" : "a number"} written out`,
      "constant",
    );
  return (arg as Extract<IR, { op: "str" | "num" }>).value;
}

/** Numbers pair with numbers and lengths with lengths; the first length decides. */
function numeric(c: Checker, args: readonly IR[]): "number" | "length" {
  const type = args.some((arg) => kindOf(arg.ty) === "length") ? "length" : "number";
  for (const arg of args) c.expect(arg, type);
  return type;
}

const SELECTABLE = new Set(["number", "bool", "string", "text", "color", null]);

function selectable(c: Checker, ty: Ty, at: Span, what: string): Ty {
  if (!SELECTABLE.has(kindOf(ty))) c.fail(at, `${what} cannot choose between ${kindOf(ty)} values`);
  return ty;
}

/** The type rules of every function; lowering builds the closures. */
export const CALLS: Readonly<Record<string, Check>> = {
  if(c, args, at) {
    arity(c, "if", args, 3, 3, at);
    c.expect(args[0], "bool");
    return selectable(c, c.common(args[1], args[2], "the branches of if"), at, "if");
  },
  case(c, args, at) {
    if (args.length < 2 || args.length % 2 !== 0) {
      c.unit.diag.fail(
        "arity",
        c.src,
        at,
        "case takes a value, pairs of key and result, and a default",
      );
    }
    const [subject, ...rest] = args;
    const last = rest.length - 1;
    for (const key of rest.filter((_, i) => i % 2 === 0 && i < last)) {
      if (!["str", "num", "color", "bool", "null"].includes(key.op))
        c.fail(key.span, "case keys must be written out", "constant");
      c.common(subject, key, "the value and its keys");
    }
    const kind = kindOf(subject.ty);
    if (kind !== null && !["string", "number", "bool", "color"].includes(kind)) {
      c.fail(subject.span, "case compares strings, numbers, bools or colors");
    }
    const results = rest.filter((_, i) => i % 2 === 1 || i === last);
    let ty = results[0].ty;
    for (const result of results.slice(1))
      ty = c.common({ ...results[0], ty }, result, "the results of case");
    return selectable(c, ty, at, "case");
  },
  trim(c, args, at) {
    arity(c, "trim", args, 2, 5, at);
    c.expect(args[0], "text");
    c.expect(args[1], "number");
    const where = args[2] ? literal(c, args[2], "str") : "end";
    if (where !== "end" && where !== "middle")
      c.fail(args[2].span, "where is 'end' or 'middle'", "value");
    for (const arg of args.slice(3)) literal(c, arg, "str");
    return "text";
  },
  fixed(c, args, at) {
    arity(c, "fixed", args, 2, 2, at);
    c.expect(args[0], "number");
    const digits = literal(c, args[1], "num") as number;
    if (!Number.isInteger(digits) || digits < 0 || digits > 100) {
      c.fail(args[1].span, "digits is a whole number from 0 to 100", "value");
    }
    return "text";
  },
  min(c, args, at) {
    arity(c, "min", args, 2, 2, at);
    return numeric(c, args);
  },
  max(c, args, at) {
    arity(c, "max", args, 2, 2, at);
    return numeric(c, args);
  },
  clamp(c, args, at) {
    arity(c, "clamp", args, 3, 3, at);
    return numeric(c, args);
  },
  alpha(c, args, at) {
    arity(c, "alpha", args, 2, 2, at);
    c.expect(args[0], "color");
    c.expect(args[1], "number");
    return "color";
  },
  ramp(c, args, at) {
    if (args.length < 5 || args.length % 2 === 0) {
      c.unit.diag.fail(
        "arity",
        c.src,
        at,
        "ramp takes a number and at least two pairs of stop and color",
      );
    }
    c.expect(args[0], "number");
    args.forEach((arg, i) => {
      if (i > 0) c.expect(arg, i % 2 === 1 ? "number" : "color");
    });
    return "color";
  },
  palette(c, args, at) {
    arity(c, "palette", args, 2, 2, at);
    c.expect(args[0], "string");
    const list = args[1];
    if (list.op !== "list" || list.items.length === 0)
      return c.fail(list.span, "palette takes a list of colors", "constant");
    for (const item of list.items) c.expect(item, "color");
    return "color";
  },
  top(c, args, at) {
    arity(c, "top", args, 1, 1, at);
    c.expect(args[0], "colors");
    return "color";
  },
  inset(c, args, at) {
    arity(c, "inset", args, 2, 2, at);
    c.expect(args[0], "box");
    c.expect(args[1], "number");
    return "box";
  },
  anchor(c, args, at) {
    arity(c, "anchor", args, 2, 2, at);
    c.expect(args[0], "box");
    const name = literal(c, args[1], "str") as string;
    if (!(name in ANCHORS))
      c.fail(
        args[1].span,
        `unknown anchor '${name}'; one of ${Object.keys(ANCHORS).join(", ")}`,
        "value",
      );
    return "point";
  },
};
