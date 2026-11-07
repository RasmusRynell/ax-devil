import { type ArithOp, type Compiled, type RowFn, arith, dynamic, konst } from "./compiled";
import type { IR } from "./ir";
import type { Lowerer } from "./lower";
import { scale, sum } from "./lowerLength";
import { coalesceText } from "./lowerText";
import { kindOf } from "./types";

type OpNode = Extract<IR, { op: "op" }>;
type Scalars = "number" | "bool" | "string" | "color";

const same = (x: number, y: number): boolean => x === y || (x !== x && y !== y);

/** The scalar type two compared operands share. */
function sharedType(l: Lowerer, a: IR, b: IR): Scalars {
  const kind = kindOf(a.ty) ?? kindOf(b.ty) ?? "bool";
  if (kind !== "number" && kind !== "bool" && kind !== "string" && kind !== "color") {
    return l.fail(a, `${kind} values cannot be compared`);
  }
  return kind;
}

function logic(l: Lowerer, node: OpNode): Compiled {
  const [a, b] = node.args.map((arg) => l.scalar(arg, "bool").fn);
  const make: Record<string, RowFn> = {
    "&&": (row) => (a(row) === 1 && b(row) === 1 ? 1 : 0),
    "||": (row) => (a(row) === 1 || b(row) === 1 ? 1 : 0),
  };
  return { type: "bool", scalar: dynamic(make[node.sym]) };
}

function compare(l: Lowerer, node: OpNode): Compiled {
  const [left, right] = node.args;
  const type = node.sym === "==" || node.sym === "!=" ? sharedType(l, left, right) : "number";
  const a = l.scalar(left, type).fn;
  const b = l.scalar(right, type).fn;
  const make: Record<string, RowFn> = {
    "==": (row) => (same(a(row), b(row)) ? 1 : 0),
    "!=": (row) => (same(a(row), b(row)) ? 0 : 1),
    "<": (row) => (a(row) < b(row) ? 1 : 0),
    "<=": (row) => (a(row) <= b(row) ? 1 : 0),
    ">": (row) => (a(row) > b(row) ? 1 : 0),
    ">=": (row) => (a(row) >= b(row) ? 1 : 0),
  };
  return { type: "bool", scalar: dynamic(make[node.sym]) };
}

function arithmetic(l: Lowerer, node: OpNode): Compiled {
  const [left, right] = node.args;
  const op = node.sym as ArithOp;
  if (node.ty === "length") {
    if (kindOf(left.ty) === "length" && kindOf(right.ty) === "length") {
      const made = sum(l.length(left), l.length(right), op as "+" | "-");
      return typeof made === "string"
        ? l.fail(node, made, "length")
        : { type: "length", length: made };
    }
    const [length, number] = kindOf(left.ty) === "length" ? [left, right] : [right, left];
    return {
      type: "length",
      length: scale(l.length(length), l.scalar(number, "number"), op as "*" | "/"),
    };
  }
  return { type: "number", scalar: arith(op, l.scalar(left, "number"), l.scalar(right, "number")) };
}

function negate(l: Lowerer, node: OpNode): Compiled {
  const [arg] = node.args;
  if (kindOf(arg.ty) === "length")
    return { type: "length", length: scale(l.length(arg), konst(-1), "*") };
  return { type: "number", scalar: arith("-", konst(0), l.scalar(arg, "number")) };
}

function coalesce(l: Lowerer, node: OpNode, target: string): Compiled {
  const [left, right] = node.args;
  if (
    target !== "text" &&
    target !== "number" &&
    target !== "bool" &&
    target !== "string" &&
    target !== "color"
  ) {
    return l.fail(node, `?? cannot choose between ${target} values`);
  }
  if (target === "text")
    return { type: "text", emit: coalesceText(l.emitter(left), l.emitter(right)) };
  const [a, b] = [l.scalar(left, target).fn, l.scalar(right, target).fn];
  const missing = target === "string" ? -1 : Number.NaN;
  return {
    type: target,
    scalar: dynamic((row) => {
      const value = a(row);
      return same(value, missing) ? b(row) : value;
    }),
  };
}

function contains(l: Lowerer, node: OpNode): Compiled {
  const [subject, list] = node.args;
  if (list.op !== "list") return l.fail(list, "expected a list");
  const type = kindOf(subject.ty) === "string" ? "string" : "number";
  const a = l.scalar(subject, type).fn;
  const members = list.items.map((item) => l.scalar(item, type).fn);
  return {
    type: "bool",
    scalar: dynamic((row) => {
      const value = a(row);
      for (let i = 0; i < members.length; i++) if (members[i](row) === value) return 1;
      return 0;
    }),
  };
}

/** Lower an operator node. */
export function lowerOp(l: Lowerer, node: OpNode, target: string): Compiled {
  switch (node.sym) {
    case "&&":
    case "||":
      return logic(l, node);
    case "!": {
      const a = l.scalar(node.args[0], "bool").fn;
      return { type: "bool", scalar: dynamic((row) => (a(row) === 1 ? 0 : 1)) };
    }
    case "==":
    case "!=":
    case "<":
    case "<=":
    case ">":
    case ">=":
      return compare(l, node);
    case "??":
      return coalesce(l, node, target);
    case "in":
      return contains(l, node);
    case "neg":
      return negate(l, node);
    default:
      return arithmetic(l, node);
  }
}
