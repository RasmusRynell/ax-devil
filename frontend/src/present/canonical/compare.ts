import type { Rows } from "./sink";

/** The absolute tolerance per number: five-decimal rounding plus the f32 columns. */
export const TOLERANCE = 2e-5;

/** Whether two oracle values are equal up to `TOLERANCE` on numbers. */
export function approx(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= TOLERANCE;
  if (Array.isArray(a) && Array.isArray(b))
    return a.length === b.length && a.every((value, i) => approx(value, b[i]));
  if (typeof a === "object" && typeof b === "object" && a !== null && b !== null) {
    const [x, y] = [a as Record<string, unknown>, b as Record<string, unknown>];
    const keys = Object.keys(x);
    return (
      keys.length === Object.keys(y).length &&
      keys.every((key) => key in y && approx(x[key], y[key]))
    );
  }
  return a === b;
}

/**
 * What differs between two lists of rows, as messages. Rows are matched one to one in any order, so a rounding tie that
 * sorts two rows differently does not matter.
 */
export function rowDifferences(
  kind: string,
  expected: readonly unknown[],
  actual: readonly unknown[],
): string[] {
  const unused = [...actual];
  const differences: string[] = [];
  for (const row of expected) {
    const index = unused.findIndex((candidate) => approx(row, candidate));
    if (index >= 0) unused.splice(index, 1);
    else differences.push(`${kind}: missing ${JSON.stringify(row)}`);
  }
  for (const row of unused) differences.push(`${kind}: unexpected ${JSON.stringify(row)}`);
  return differences;
}

const KINDS = [
  "boxes",
  "circles",
  "lines",
  "points",
  "polygons",
  "polylines",
  "texts",
  "labels",
] as const;

/** Every difference between the oracle's instructions and the recorded rows. */
export function compareRows(expected: Partial<Record<string, unknown[]>>, actual: Rows): string[] {
  return KINDS.flatMap((kind) => rowDifferences(kind, expected[kind] ?? [], actual[kind]));
}
