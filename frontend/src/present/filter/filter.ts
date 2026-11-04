import { FILTER } from "../expr/check";
import type { RowFn } from "../expr/compiled";
import { compileExpression } from "../expr/compile";
import type { EvalContext } from "../expr/context";
import type { Diagnostic } from "../expr/diagnostic";
import type { SampleView } from "../expr/sampleShape";

/** A frame-filter expression ready to run; `test` is null for the empty expression, which keeps every row. */
export interface CompiledFilter {
  readonly ctx: EvalContext | null;
  readonly test: RowFn | null;
}

/** What compiling a frame filter gives: the filter, or every problem found. */
export type FilterResult =
  | { readonly ok: true; readonly filter: CompiledFilter }
  | { readonly ok: false; readonly diagnostics: readonly Diagnostic[] };

/** Compile a frame filter: an expression over an entity's fields (no variables, no lengths) that is true to keep it. */
export function compileFilter(expression: string): FilterResult {
  if (expression.trim() === "") return { ok: true, filter: { ctx: null, test: null } };
  const result = compileExpression(expression, FILTER, "bool");
  if (!result.ok) return result;
  const { compiled, ctx } = result.expression;
  return { ok: true, filter: { ctx, test: compiled.type === "bool" ? compiled.scalar.fn : null } };
}

/** A number a classification can carry, filterable by range; each is also the name of its field in an expression. */
export type Measure = "score" | "confidence";

/** Every measure, in the order a menu lists them. */
export const MEASURES: readonly Measure[] = ["score", "confidence"];

/** An inclusive range; `null` is an open end. A range judges only values that were reported. */
export type MeasureRange = { readonly min: number | null; readonly max: number | null };

/** The range of each measure. */
export type MeasureRanges = Readonly<Record<Measure, MeasureRange>>;

/** Ranges that keep every row. */
export const OPEN_RANGES: MeasureRanges = {
  score: { min: null, max: null },
  confidence: { min: null, max: null },
};

/** A number as the expression language reads it, which has no exponent: `String(n)` with any exponent written out. */
function literal(n: number): string {
  const text = String(n);
  const parts = /^(-?)(\d)(?:\.(\d+))?e([+-]\d+)$/.exec(text);
  if (parts === null) return text;
  const [, sign, lead, fraction = "", exponent] = parts;
  const digits = `${lead}${fraction}`;
  const point = 1 + Number(exponent);
  if (point <= 0) return `${sign}0.${"0".repeat(-point)}${digits}`;
  return `${sign}${digits.padEnd(point, "0")}`;
}

/** The test that keeps a row whose `measure` is absent or inside `range`; null for an open range. */
function rangeClause(measure: Measure, { min, max }: MeasureRange): string | null {
  const bounds: string[] = [];
  if (min !== null && Number.isFinite(min)) bounds.push(`${measure} >= ${literal(min)}`);
  if (max !== null && Number.isFinite(max)) bounds.push(`${measure} <= ${literal(max)}`);
  return bounds.length === 0 ? null : `(${measure} == null || (${bounds.join(" && ")}))`;
}

/**
 * The one expression that keeps the rows inside every range and passing the authored `expression`. A row with no
 * reported value for a measure is not judged by that measure; the authored expression keeps its own rules. With open
 * ranges it is `expression` itself.
 */
export function withRanges(expression: string, ranges: MeasureRanges): string {
  const clauses = MEASURES.flatMap((measure) => rangeClause(measure, ranges[measure]) ?? []);
  if (clauses.length === 0) return expression;
  if (expression.trim() !== "") clauses.push(`(${expression})`);
  return clauses.join(" && ");
}

/** Which class options a lane has switched on: option `i` of `options` is bit `i % 32` of word `i / 32`. */
export interface ClassMask {
  readonly enabled: Uint32Array;
  readonly options: number;
}

/** The rows kept and the relations between kept rows; the arrays are reused from one sample to the next. */
export class RowSet {
  count = 0;
  rows = new Uint32Array(0);
  /** 1 for each kept entity row. */
  kept = new Uint8Array(0);
  relationCount = 0;
  relations = new Uint32Array(0);

  /** Make room for a sample; grows by doubling and never shrinks. */
  reserve(rows: number, relations: number): void {
    if (this.rows.length < rows) {
      const size = Math.max(rows, this.rows.length * 2);
      this.rows = new Uint32Array(size);
      this.kept = new Uint8Array(size);
    }
    if (this.relations.length < relations)
      this.relations = new Uint32Array(Math.max(relations, this.relations.length * 2));
  }
}

/**
 * Whether the `words` mask words of a row, from `at`, pass the options: no options means everything; otherwise the
 * row needs an enabled class, or, with something enabled, no class that is switched off (rows of classes the decoder
 * leaves unlisted stay).
 */
export function maskKeeps(rowMask: Uint32Array, at: number, mask: ClassMask): boolean {
  const { enabled, options } = mask;
  if (options === 0) return true;
  let anyEnabled = false;
  let switchedOff = false;
  for (let word = 0; word < enabled.length; word++) {
    const row = rowMask[at + word];
    if ((row & enabled[word]) !== 0) return true;
    anyEnabled ||= enabled[word] !== 0;
    switchedOff ||= (row & ~enabled[word]) !== 0;
  }
  return anyEnabled && !switchedOff;
}

/** Keep the rows that pass the class mask and the filter, and the relations whose two ends are both kept. */
export function filterRows(
  sample: SampleView,
  filter: CompiledFilter | null,
  mask: ClassMask,
  out: RowSet = new RowSet(),
): RowSet {
  if (mask.enabled.length !== sample.maskWords)
    throw new Error(`${mask.enabled.length} enabled words for a sample of ${sample.maskWords}`);
  out.reserve(sample.rows, sample.relationCount);
  const test = filter?.test ?? null;
  filter?.ctx?.bind(sample);
  let count = 0;
  for (let row = 0; row < sample.rows; row++) {
    const keep =
      maskKeeps(sample.classMask, row * sample.maskWords, mask) &&
      (test === null || test(row) === 1);
    out.kept[row] = keep ? 1 : 0;
    if (keep) out.rows[count++] = row;
  }
  out.count = count;
  let relations = 0;
  for (let i = 0; i < sample.relationCount; i++) {
    const [source, target] = [sample.relSource[i], sample.relTarget[i]];
    if (source >= 0 && target >= 0 && out.kept[source] === 1 && out.kept[target] === 1)
      out.relations[relations++] = i;
  }
  out.relationCount = relations;
  return out;
}
