import type { Ast } from "./ast";
import { type Source, type Span, didYouMean } from "./diagnostic";
import { FIELDS } from "./fields";
import type { IR, Side } from "./ir";
import { CALLS } from "./checkCall";
import { type ExprType, type Ty, TypeVar, join, kindOf, unify } from "./types";
import type { ExprUnit } from "./unit";

/** Which names an expression may use. */
export interface Scope {
  readonly fields: "entity" | "relation" | "none";
  readonly vars: boolean;
  readonly lengths: boolean;
}

/** Variable defaults, rule values and mark fields of entity layers. */
export const ENTITY: Scope = { fields: "entity", vars: true, lengths: true };
/** Rule conditions: fields only. */
export const CONDITION: Scope = { fields: "entity", vars: false, lengths: false };
/** Fields of relation layers: the relation and its two ends. */
export const RELATION: Scope = { fields: "relation", vars: false, lengths: true };
/** Frame filters: entity fields without vars or lengths. */
export const FILTER: Scope = { fields: "entity", vars: false, lengths: false };
/** Theme values: constants only. */
export const CONSTANT: Scope = { fields: "none", vars: false, lengths: true };

const COMPARABLE = new Set<ExprType>(["number", "bool", "string", "color"]);

/** Type-checks one expression into IR, recording problems against its source and leaving at the first. */
export class Checker {
  constructor(
    readonly unit: ExprUnit,
    readonly scope: Scope,
    readonly src: Source,
  ) {}

  fail(
    span: Span,
    message: string,
    code: "type" | "arity" | "constant" | "unknown-name" | "syntax" | "value" = "type",
  ): never {
    return this.unit.diag.fail(code, this.src, span, message);
  }

  /** Check `ast` and require it to be `want`. */
  checkAs(ast: Ast, want: ExprType): IR {
    const ir = this.check(ast);
    this.expect(ir, want);
    return ir;
  }

  /**
   * Require a type; an open type is narrowed, and a string is accepted as text where it is used, without becoming text
   * itself, so a string variable that is displayed stays comparable.
   */
  expect(ir: IR, want: ExprType): void {
    if (want === "text" && kindOf(ir.ty) === "string") return;
    if (unify(ir.ty, want)) return;
    const found = kindOf(ir.ty) ?? "unknown";
    if (found === "length" && (want === "bool" || want === "number")) {
      this.fail(ir.span, "lengths resolve at layout; use place or min_size");
    }
    this.fail(ir.span, `expected ${want}, found ${found}`);
  }

  /** The type two branches share, narrowing open types to each other. */
  common(a: IR, b: IR, what: string): Ty {
    if (!unify(a.ty, b.ty)) {
      this.fail(
        b.span,
        `${what} mix ${kindOf(a.ty) ?? "unknown"} and ${kindOf(b.ty) ?? "unknown"}`,
      );
    }
    if (typeof a.ty !== "string") return a.ty;
    if (typeof b.ty !== "string") return b.ty;
    return join(a.ty, b.ty) ?? a.ty;
  }

  check(ast: Ast): IR {
    switch (ast.kind) {
      case "num":
        return {
          op: "num",
          value: ast.value,
          px: ast.px,
          ty: ast.px ? "length" : "number",
          span: ast.span,
        };
      case "str":
        return { op: "str", value: ast.value, ty: "string", span: ast.span };
      case "color":
        return { op: "color", value: ast.value, ty: "color", span: ast.span };
      case "lit":
        return ast.value === "null"
          ? { op: "null", ty: new TypeVar(), span: ast.span }
          : { op: "bool", value: ast.value === "true", ty: "bool", span: ast.span };
      case "path":
        return this.path(ast.segments, ast.span);
      case "list":
        return this.fail(
          ast.span,
          "a list is only allowed after 'in' and as the colors of palette",
        );
      case "call":
        return this.call(ast);
      case "unary":
        return this.unary(ast);
      case "binary":
        return this.binary(ast);
      case "in":
        return this.contains(ast);
    }
  }

  /** A list of constants, for `in` and `palette`. */
  list(ast: Ast): IR {
    if (ast.kind !== "list") return this.fail(ast.span, "expected a list like [a, b]");
    const items = ast.items.map((item) => this.check(item));
    for (const item of items) {
      if (!["num", "str", "color", "bool"].includes(item.op))
        this.fail(item.span, "list items must be literals", "constant");
    }
    let ty: Ty = new TypeVar();
    for (const item of items) {
      if (!unify(ty, item.ty)) this.fail(item.span, "list items must all have the same type");
      ty = typeof item.ty === "string" ? item.ty : ty;
    }
    return { op: "list", items, ty, span: ast.span };
  }

  private path(segments: readonly string[], span: Span): IR {
    const [first] = segments;
    const variable = this.unit.vars.get(first);
    if (variable && segments.length === 1) {
      if (!this.scope.vars)
        this.fail(
          span,
          `variable '${first}' is not available here; only fields are`,
          "unknown-name",
        );
      return { op: "var", name: first, ty: variable.type, span };
    }
    let side: Side = "self";
    let rest = segments;
    if (this.scope.fields === "relation" && (first === "source" || first === "target")) {
      side = first;
      rest = segments.slice(1);
    }
    if (this.scope.fields !== "none" && rest[0] === "attr") {
      if (rest.length !== 2)
        this.fail(span, "an attribute is written attr.name or attr['odd name']", "unknown-name");
      let type = this.unit.attrTypes.get(rest[1]);
      if (!type) this.unit.attrTypes.set(rest[1], (type = new TypeVar()));
      return { op: "attr", name: rest[1], side, ty: type, span };
    }
    const name = rest.join(".");
    const def = this.scope.fields === "none" ? undefined : FIELDS[name];
    const wrongScope =
      def &&
      (def.relationOnly
        ? this.scope.fields !== "relation" || side !== "self"
        : this.scope.fields === "relation" && side === "self");
    if (!def || wrongScope) {
      const known = Object.entries(FIELDS)
        .filter(([, d]) =>
          this.scope.fields === "relation" ? d.relationOnly || !d.isLength : !d.relationOnly,
        )
        .map(([n]) => n);
      const names = [...known, ...(this.scope.vars ? this.unit.vars.keys() : [])];
      return this.fail(
        span,
        `unknown name '${segments.join(".")}'${didYouMean(segments.join("."), names)}`,
        "unknown-name",
      );
    }
    if (def.isLength && (!this.scope.lengths || side !== "self")) {
      this.fail(span, "lengths are not available here; they resolve at layout", "unknown-name");
    }
    return { op: "field", name, side, ty: def.type, span };
  }

  private call(ast: Extract<Ast, { kind: "call" }>): IR {
    const check = CALLS[ast.name];
    if (!check) {
      return this.fail(
        ast.span,
        `unknown function '${ast.name}'${didYouMean(ast.name, Object.keys(CALLS))}`,
        "unknown-name",
      );
    }
    const args = ast.args.map((arg) => (arg.kind === "list" ? this.list(arg) : this.check(arg)));
    return { op: "call", name: ast.name, args, ty: check(this, args, ast.span), span: ast.span };
  }

  private unary(ast: Extract<Ast, { kind: "unary" }>): IR {
    const arg = this.check(ast.arg);
    if (ast.op === "!") {
      this.expect(arg, "bool");
      return { op: "op", sym: "!", args: [arg], ty: "bool", span: ast.span };
    }
    if (kindOf(arg.ty) !== "length") this.expect(arg, "number");
    return {
      op: "op",
      sym: "neg",
      args: [arg],
      ty: kindOf(arg.ty) === "length" ? "length" : "number",
      span: ast.span,
    };
  }

  private binary(ast: Extract<Ast, { kind: "binary" }>): IR {
    const left = this.check(ast.left);
    const right = this.check(ast.right);
    const node = (ty: Ty): IR => ({
      op: "op",
      sym: ast.op,
      args: [left, right],
      ty,
      span: ast.span,
    });
    switch (ast.op) {
      case "&&":
      case "||":
        this.expect(left, "bool");
        this.expect(right, "bool");
        return node("bool");
      case "<":
      case "<=":
      case ">":
      case ">=":
        this.expect(left, "number");
        this.expect(right, "number");
        return node("bool");
      case "==":
      case "!=": {
        this.common(left, right, "compared values");
        const kind = kindOf(left.ty) ?? kindOf(right.ty);
        if (kind !== null && !COMPARABLE.has(kind))
          this.fail(ast.span, `${kind} values cannot be compared`);
        return node("bool");
      }
      case "??": {
        const ty = this.common(left, right, "the sides of ??");
        const kind = kindOf(ty);
        if (kind === "length" || kind === "box" || kind === "point" || kind === "colors") {
          this.fail(ast.span, `?? cannot choose between ${kind} values`);
        }
        return node(ty);
      }
      default:
        return node(this.arithmetic(ast.op, left, right));
    }
  }

  private arithmetic(op: string, left: IR, right: IR): ExprType {
    const l = kindOf(left.ty);
    const r = kindOf(right.ty);
    if (op === "+" || op === "-") {
      const lengths = l === "length" || r === "length";
      this.expect(left, lengths ? "length" : "number");
      this.expect(right, lengths ? "length" : "number");
      return lengths ? "length" : "number";
    }
    if (op === "*" && r === "length") {
      this.expect(left, "number");
      return "length";
    }
    if (l === "length") {
      this.expect(right, "number");
      return "length";
    }
    this.expect(left, "number");
    this.expect(right, "number");
    return "number";
  }

  private contains(ast: Extract<Ast, { kind: "in" }>): IR {
    const left = this.check(ast.left);
    const list = this.list(ast.list);
    this.common(left, list, "the value and the list");
    const kind = kindOf(left.ty);
    if (kind !== null && kind !== "string" && kind !== "number")
      this.fail(left.span, `${kind} values cannot be looked up in a list`);
    return { op: "op", sym: "in", args: [left, list], ty: "bool", span: ast.span };
  }
}
