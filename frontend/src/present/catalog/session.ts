import type { Ast } from "../expr/ast";
import { Checker, type Scope } from "../expr/check";
import type { Compiled } from "../expr/compiled";
import type { Source } from "../expr/diagnostic";
import type { IR } from "../expr/ir";
import { Lowerer, type VarSlot } from "../expr/lower";
import { parseExpr } from "../expr/parse";
import { type ExprType, type Ty, unify } from "../expr/types";
import { ExprUnit } from "../expr/unit";

/** An expression string read and parsed, not yet checked. */
export interface Parsed {
  readonly src: Source;
  readonly ast: Ast;
}

/** A checked expression waiting for the types of the whole catalog to settle before it becomes closures. */
export interface Checked {
  readonly src: Source;
  readonly ir: IR;
}

/** One catalog compile: the shared unit, and the lowering work deferred until every variable's type is known. */
export class Session {
  readonly unit = new ExprUnit();
  readonly slots = new Map<string, VarSlot>();
  private readonly deferred: (() => void)[] = [];

  get diag() {
    return this.unit.diag;
  }

  /** Read an expression: a string is one; a number, boolean or null is the literal it writes. */
  parse(value: unknown, pointer: string): Parsed | null {
    const literal = value === null || typeof value === "number" || typeof value === "boolean";
    if (typeof value !== "string" && !literal) {
      this.diag.add(
        "value",
        { pointer, spans: false },
        null,
        "expected a literal or an expression string",
      );
      return null;
    }
    const src: Source = {
      text: literal ? String(value) : (value as string),
      pointer,
      spans: !literal,
    };
    const ast = parseExpr(src, this.diag);
    return ast ? { src, ast } : null;
  }

  /** Check a parsed expression in `scope`; `want` fixes its type. */
  check(parsed: Parsed, scope: Scope, want?: ExprType): Checked | null {
    return this.diag.attempt(() => {
      const checker = new Checker(this.unit, scope, parsed.src);
      const ir = checker.check(parsed.ast);
      if (want) checker.expect(ir, want);
      return { src: parsed.src, ir };
    });
  }

  /** Make a checked expression the same type as a variable. */
  agree(checked: Checked, type: Ty, name: string): boolean {
    if (unify(checked.ir.ty, type)) return true;
    this.diag.add(
      "type",
      checked.src,
      checked.ir.span,
      `this value does not fit the type of variable '${name}'`,
    );
    return false;
  }

  /** Parse and check in one step. */
  expression(value: unknown, pointer: string, scope: Scope, want?: ExprType): Checked | null {
    const parsed = this.parse(value, pointer);
    return parsed ? this.check(parsed, scope, want) : null;
  }

  /** Lower a checked expression now (types are final). */
  lower(checked: Checked, want: ExprType): Compiled | null {
    return this.diag.attempt(() =>
      new Lowerer(this.unit.ctx, checked.src, this.diag, this.slots).lower(checked.ir, want),
    );
  }

  /** Run `work` once all variable types are final. */
  later(work: () => void): void {
    this.deferred.push(work);
  }

  /** Check an expression and lower it later into `put`. */
  field(
    value: unknown,
    pointer: string,
    scope: Scope,
    want: ExprType,
    put: (compiled: Compiled) => void,
  ): void {
    const checked = this.expression(value, pointer, scope, want);
    if (checked)
      this.later(() => {
        const compiled = this.lower(checked, want);
        if (compiled) put(compiled);
      });
  }

  /** Run the deferred lowering in the order it was queued. */
  finish(): void {
    for (const work of this.deferred) work();
  }
}
