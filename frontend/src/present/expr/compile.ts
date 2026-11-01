import { type Scope, Checker } from "./check";
import type { Compiled } from "./compiled";
import type { EvalContext } from "./context";
import type { Diagnostic, Source } from "./diagnostic";
import { Lowerer } from "./lower";
import { parseExpr } from "./parse";
import type { ExprType } from "./types";
import { ExprUnit } from "./unit";

/** An expression compiled on its own: its closures and the context they read. */
export interface CompiledExpression {
  readonly compiled: Compiled;
  readonly ctx: EvalContext;
}

/** What compiling can give: a result, or every problem found. */
export type CompileResult =
  | { readonly ok: true; readonly expression: CompiledExpression }
  | { readonly ok: false; readonly diagnostics: readonly Diagnostic[] };

/** Where an expression's problems are reported: the pointer of its JSON string. */
export function sourceOf(text: string, pointer = "", spans = true): Source {
  return { text, pointer, spans };
}

/** Compile one expression of type `expected` in `scope`, with no variables. */
export function compileExpression(
  text: string,
  scope: Scope,
  expected: ExprType,
  pointer = "",
): CompileResult {
  const unit = new ExprUnit();
  const src = sourceOf(text, pointer);
  const compiled = compileIn(unit, src, scope, expected);
  if (compiled === null) return { ok: false, diagnostics: unit.diag.list };
  return { ok: true, expression: { compiled, ctx: unit.ctx } };
}

function compileIn(unit: ExprUnit, src: Source, scope: Scope, expected: ExprType): Compiled | null {
  const ast = parseExpr(src, unit.diag);
  if (!ast) return null;
  return unit.diag.attempt(() => {
    const ir = new Checker(unit, scope, src).checkAs(ast, expected);
    return new Lowerer(unit.ctx, src, unit.diag, new Map()).lower(ir, expected);
  });
}
