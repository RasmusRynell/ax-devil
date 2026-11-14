import type { Ast } from "../expr/ast";
import type { BasisValue, Compiled } from "../expr/compiled";
import type { EvalContext } from "../expr/context";
import type { Span } from "../expr/diagnostic";
import type { VarSlot } from "../expr/lower";
import { MAX_DEPTH } from "../expr/parse";
import { type ExprType, type TypeVar, kindOf } from "../expr/types";
import type { Checked, Parsed, Session } from "./session";
import type { Setter, VarProgram } from "./types";

/** A declared variable with everything written about it in the document. */
export interface VarDecl {
  readonly name: string;
  readonly pointer: string;
  readonly type: TypeVar;
  readonly initial: Parsed | null;
  readonly assignments: { readonly rule: number; readonly parsed: Parsed }[];
  /** The value a theme gives as the default, by theme index (1-based). */
  readonly themed: { readonly theme: number; readonly parsed: Parsed }[];
  initialChecked: Checked | null;
  assignmentsChecked: { readonly rule: number; readonly checked: Checked }[];
  themedChecked: { readonly theme: number; readonly checked: Checked }[];
}

/** Every name path starts with, and where. */
function references(
  ast: Ast,
  found: { name: string; span: Span }[] = [],
): { name: string; span: Span }[] {
  switch (ast.kind) {
    case "path":
      found.push({ name: ast.segments[0], span: ast.span });
      break;
    case "call":
    case "list":
      for (const child of ast.kind === "call" ? ast.args : ast.items) references(child, found);
      break;
    case "unary":
      references(ast.arg, found);
      break;
    case "binary":
      references(ast.left, found);
      references(ast.right, found);
      break;
    case "in":
      references(ast.left, found);
      references(ast.list, found);
      break;
    default:
      break;
  }
  return found;
}

/** The variables in an order where each comes after those it reads; a cycle is a problem on the reading expression. */
export function orderVars(decls: ReadonlyMap<string, VarDecl>, session: Session): VarDecl[] {
  const ordered: VarDecl[] = [];
  const state = new Map<string, "visiting" | "done">();
  const visit = (decl: VarDecl, path: string[]): void => {
    if (path.length > MAX_DEPTH) {
      const at = { pointer: decl.pointer, spans: false };
      session.diag.add("limit", at, null, `variables read each other more than ${MAX_DEPTH} deep`);
      state.set(decl.name, "done");
      return;
    }
    state.set(decl.name, "visiting");
    const expressions = [decl.initial, ...decl.assignments.map((a) => a.parsed)].filter(
      (p): p is Parsed => p !== null,
    );
    for (const parsed of expressions) {
      for (const { name, span } of references(parsed.ast)) {
        const next = decls.get(name);
        if (!next) continue;
        if (state.get(name) === "visiting") {
          const loop = [...path.slice(path.indexOf(name)), name].join(" -> ");
          session.diag.add("cycle", parsed.src, span, `variables depend on each other: ${loop}`);
        } else if (!state.has(name)) {
          visit(next, [...path, next.name]);
        }
      }
    }
    state.set(decl.name, "done");
    ordered.push(decl);
  };
  for (const decl of decls.values()) if (!state.has(decl.name)) visit(decl, [decl.name]);
  return ordered;
}

const REGISTERS: Partial<Record<ExprType, number>> = {
  number: 1,
  bool: 1,
  string: 1,
  color: 1,
  length: 4,
  text: 2,
};

/** Fix every variable's type and place it in the registers; returns the registers per row. */
export function allocate(order: readonly VarDecl[], session: Session): number {
  let next = 0;
  for (const decl of order) {
    const type = kindOf(decl.type);
    const size = type === null ? undefined : REGISTERS[type];
    if (type === null || size === undefined) {
      const message =
        type === null
          ? `the type of variable '${decl.name}' is not determined by anything`
          : `a variable cannot hold ${type}`;
      session.diag.add(
        type === null ? "infer" : "type",
        { pointer: decl.pointer, spans: false },
        null,
        message,
      );
      continue;
    }
    session.slots.set(decl.name, {
      name: decl.name,
      type,
      register: next,
      length: null,
      constant: null,
    });
    next += size;
  }
  return next;
}

function setterFor(ctx: EvalContext, register: number, compiled: Compiled): Setter {
  switch (compiled.type) {
    case "length": {
      const { px, k, lo, hi } = compiled.length;
      return (row) => {
        const base = row * ctx.stride + register;
        ctx.regs[base] = px.fn(row);
        ctx.regs[base + 1] = k.fn(row);
        ctx.regs[base + 2] = lo.fn(row);
        ctx.regs[base + 3] = hi.fn(row);
      };
    }
    case "text": {
      const emit = compiled.emit;
      return (row) => {
        const base = row * ctx.stride + register;
        const pool = ctx.textPool;
        const start = pool.length;
        if (emit(row, pool)) {
          ctx.regs[base] = start;
          ctx.regs[base + 1] = pool.length - start;
        } else {
          pool.length = start;
          ctx.regs[base] = Number.NaN;
          ctx.regs[base + 1] = 0;
        }
      };
    }
    case "colors":
    case "box":
    case "point":
      return () => undefined;
    default: {
      const read = compiled.scalar.fn;
      return (row) => {
        ctx.regs[row * ctx.stride + register] = read(row);
      };
    }
  }
}

/** Whether a compiled value is the same for every row. */
function isConstant(compiled: Compiled): boolean {
  switch (compiled.type) {
    case "number":
    case "bool":
    case "color":
      return compiled.scalar.constant !== undefined;
    case "length": {
      const { px, k, lo, hi } = compiled.length;
      return [px, k, lo, hi].every((part) => part.constant !== undefined);
    }
    default:
      return false;
  }
}

/** Lower every variable (in dependency order) into setters; the lengths of one variable must share a basis. */
export function lowerVars(
  order: readonly VarDecl[],
  session: Session,
  themeCount: number,
): VarProgram[] {
  const ctx = session.unit.ctx;
  const programs: VarProgram[] = [];
  for (const decl of order) {
    const slot = session.slots.get(decl.name);
    if (!slot || !decl.initialChecked) continue;
    const lowered = (checked: Checked): Compiled | null => session.lower(checked, slot.type);
    const initial = lowered(decl.initialChecked);
    const assignments = decl.assignmentsChecked.map((a) => ({
      rule: a.rule,
      compiled: lowered(a.checked),
      checked: a.checked,
    }));
    const themed = decl.themedChecked.map((t) => ({
      theme: t.theme,
      compiled: lowered(t.checked),
      checked: t.checked,
    }));
    if (!initial || assignments.some((a) => !a.compiled) || themed.some((t) => !t.compiled))
      continue;
    if (slot.type === "length")
      joinLengths(session, decl, slot, [
        initial,
        ...assignments.map((a) => a.compiled),
        ...themed.map((t) => t.compiled),
      ] as Compiled[]);
    // No rule or theme sets it and it reads nothing: it is a constant, and neither registers nor rows are involved.
    if (assignments.length === 0 && themed.length === 0 && isConstant(initial)) {
      slot.constant = initial;
      continue;
    }
    const setter = (compiled: Compiled): Setter => setterFor(ctx, slot.register, compiled);
    const defaults = Array.from({ length: themeCount + 1 }, (_, theme) => {
      const override = themed.find((t) => t.theme === theme);
      return setter(override ? (override.compiled as Compiled) : initial);
    });
    programs.push({
      name: decl.name,
      defaults,
      assignments: assignments.map((a) => ({ rule: a.rule, set: setter(a.compiled as Compiled) })),
    });
  }
  return programs;
}

function joinLengths(
  session: Session,
  decl: VarDecl,
  slot: VarSlot,
  values: readonly Compiled[],
): void {
  let basis = 0;
  let clamped = false;
  for (const value of values) {
    if (value.type !== "length") continue;
    if (value.length.basis !== 0 && basis !== 0 && value.length.basis !== basis) {
      session.diag.add(
        "length",
        { pointer: decl.pointer, spans: false },
        null,
        `variable '${decl.name}' is relative to more than one of box.w, box.h, box.min, image.w, image.h, image.min`,
      );
    }
    basis = basis || value.length.basis;
    clamped = clamped || value.length.clamped;
  }
  slot.length = { basis: basis as BasisValue, clamped };
}
