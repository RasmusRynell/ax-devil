import {
  type CBox,
  type CLength,
  type Compiled,
  type Emitter,
  type Scalar,
  dynamic,
  konst,
} from "./compiled";
import type { EvalContext } from "./context";
import type { Diagnostics, DiagnosticCode, Source, Span } from "./diagnostic";
import { FIELDS, through } from "./fields";
import type { IR } from "./ir";
import { lowerCall } from "./lowerCalls";
import { constantLength } from "./lowerLength";
import { lowerOp } from "./lowerOps";
import { constantText, noText, storedText, stringText } from "./lowerText";
import { AttrKind, type AttrKindValue } from "./sampleShape";
import { type ExprType, kindOf } from "./types";

/** Where a variable lives in the registers, and what is known of it once it is lowered. */
export interface VarSlot {
  readonly name: string;
  readonly type: ExprType;
  readonly register: number;
  /** For lengths: the basis and clamping every assignment agrees on; set when the variable is lowered. */
  length: { readonly basis: CLength["basis"]; readonly clamped: boolean } | null;
  /** What the variable is for every row and theme when no rule or theme can change it; reads compile to this. */
  constant: Compiled | null;
}

const ATTR_KINDS: Readonly<Partial<Record<ExprType, AttrKindValue>>> = {
  bool: AttrKind.Bool,
  number: AttrKind.Number,
  string: AttrKind.String,
  text: AttrKind.String,
  colors: AttrKind.Colors,
};

type Scalars = "number" | "bool" | "string" | "color";

/** Turns checked IR into closures over the shared context. Failures are recorded against the source and thrown. */
export class Lowerer {
  constructor(
    readonly ctx: EvalContext,
    readonly src: Source,
    readonly diag: Diagnostics,
    readonly vars: ReadonlyMap<string, VarSlot>,
  ) {}

  fail(at: IR | Span, message: string, code: DiagnosticCode = "type"): never {
    return this.diag.fail(
      code,
      this.src,
      Array.isArray(at) ? (at as Span) : (at as IR).span,
      message,
    );
  }

  /** Lower `node` to `want` (default: its own type). A string becomes text where text is wanted. */
  lower(node: IR, want?: ExprType): Compiled {
    const target = want ?? kindOf(node.ty) ?? "bool";
    const made = this.make(node, target);
    if (made.type === target) return made;
    if (made.type === "string" && target === "text")
      return { type: "text", emit: stringText(this.ctx, made.scalar.fn) };
    return this.fail(node, `expected ${target}, found ${made.type}`);
  }

  private make(node: IR, target: ExprType): Compiled {
    switch (node.op) {
      case "num":
        return node.px
          ? { type: "length", length: constantLength(node.value) }
          : { type: "number", scalar: konst(node.value) };
      case "str":
        return target === "text"
          ? { type: "text", emit: constantText(node.value) }
          : { type: "string", scalar: this.literal(node.value) };
      case "color":
        return { type: "color", scalar: konst(node.value) };
      case "bool":
        return { type: "bool", scalar: konst(node.value ? 1 : 0) };
      case "null":
        return this.nothing(node, target);
      case "field":
        return this.field(node);
      case "attr":
        return this.attr(node);
      case "var":
        return this.variable(node);
      case "list":
        return this.fail(node, "a list is only allowed after 'in' and as the colors of palette");
      case "call":
        return lowerCall(this, node, target);
      case "op":
        return lowerOp(this, node, target);
    }
  }

  /** A string literal's id in the current sample. */
  literal(text: string): Scalar {
    const slot = this.ctx.literal(text);
    const ctx = this.ctx;
    return dynamic(() => ctx.lit[slot]);
  }

  private nothing(node: IR, target: ExprType): Compiled {
    switch (target) {
      case "string":
        return { type: "string", scalar: konst(-1) };
      case "text":
        return { type: "text", emit: noText };
      case "number":
      case "bool":
      case "color":
        return { type: target, scalar: konst(Number.NaN) };
      default:
        return this.fail(node, `null cannot be a ${target}`);
    }
  }

  private field(node: Extract<IR, { op: "field" }>): Compiled {
    return FIELDS[node.name].lower(this.ctx, node.side);
  }

  private attr(node: Extract<IR, { op: "attr" }>): Compiled {
    const ctx = this.ctx;
    const kind = kindOf(node.ty);
    const slot = ctx.attrSlot(node.name, kind === null ? null : (ATTR_KINDS[kind] ?? null));
    const read = (type: Scalars, missing: number, at: (entity: number) => number): Compiled => ({
      type,
      scalar: dynamic(through(ctx, node.side, at, missing)),
    });
    switch (kind) {
      case null:
        // Nothing fixes its type, so only whether it is there can be asked.
        return read("bool", Number.NaN, (e) =>
          ctx.attrBool[slot][e] !== 2 ||
          ctx.attrNumber[slot][e] === ctx.attrNumber[slot][e] ||
          ctx.attrId[slot][e] >= 0 ||
          ctx.attrSpan[slot][2 * e] >= 0
            ? 1
            : Number.NaN,
        );
      case "bool":
        return read("bool", Number.NaN, (e) =>
          ctx.attrBool[slot][e] === 2 ? Number.NaN : ctx.attrBool[slot][e],
        );
      case "number":
        return read("number", Number.NaN, (e) => ctx.attrNumber[slot][e]);
      case "string":
      case "text":
        return read("string", -1, (e) => ctx.attrId[slot][e]);
      case "colors":
        return { type: "colors", slot, side: node.side };
      default:
        return this.fail(node, `an attribute cannot be a ${kind}`);
    }
  }

  private variable(node: Extract<IR, { op: "var" }>): Compiled {
    const ctx = this.ctx;
    const slot = this.vars.get(node.name);
    if (!slot) return this.fail(node, `unknown variable '${node.name}'`, "unknown-name");
    if (slot.constant) return slot.constant;
    const at = slot.register;
    const reg = (offset: number): Scalar =>
      dynamic((row) => ctx.regs[row * ctx.stride + at + offset]);
    switch (slot.type) {
      case "number":
      case "bool":
      case "string":
      case "color":
        return { type: slot.type, scalar: reg(0) };
      case "text":
        return { type: "text", emit: storedText(ctx, at) };
      case "length": {
        if (!slot.length)
          return this.fail(
            node,
            `variable '${node.name}' is used before its value is known`,
            "cycle",
          );
        const { basis, clamped } = slot.length;
        return {
          type: "length",
          length: { px: reg(0), k: reg(1), lo: reg(2), hi: reg(3), basis, clamped },
        };
      }
      default:
        return this.fail(node, `a variable cannot be a ${slot.type}`);
    }
  }

  /** `node` as a scalar of `type`. */
  scalar(node: IR, type: Scalars): Scalar {
    const made = this.lower(node, type);
    if (!("scalar" in made)) return this.fail(node, `expected ${type}, found ${made.type}`);
    return made.scalar;
  }

  emitter(node: IR): Emitter {
    const made = this.lower(node, "text");
    if (made.type !== "text") return this.fail(node, `expected text, found ${made.type}`);
    return made.emit;
  }

  length(node: IR): CLength {
    const made = this.lower(node, "length");
    if (made.type !== "length") return this.fail(node, `expected length, found ${made.type}`);
    return made.length;
  }

  box(node: IR): CBox {
    const made = this.lower(node, "box");
    if (made.type !== "box") return this.fail(node, `expected box, found ${made.type}`);
    return made.box;
  }

  /** The value of a scalar that must be written out. */
  constant(node: IR, type: "number" | "color"): number {
    const value = this.scalar(node, type).constant;
    if (value === undefined) return this.fail(node, `expected a ${type} written out`, "constant");
    return value;
  }
}
