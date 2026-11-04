import type { Diagnostic } from "../expr/diagnostic";
import { compileCatalog } from "./compile";
import { locate } from "./locate";
import { type Sheet, exercise } from "./sheets";

export type { Sheet } from "./sheets";

/** What `catalog check` says about a document. */
export interface CheckReport {
  readonly ok: boolean;
  readonly text: string;
}

/** The problems as lines, `LINE:COLUMN POINTER: MESSAGE [code]`, in document order. */
function lines(diagnostics: readonly Diagnostic[], source: string): string[] {
  return diagnostics
    .map((diagnostic) => ({ diagnostic, at: locate(diagnostic, source) }))
    .sort(
      (a, b) => (a.at?.line ?? 0) - (b.at?.line ?? 0) || (a.at?.column ?? 0) - (b.at?.column ?? 0),
    )
    .map(({ diagnostic, at }) => {
      const place = at ? `${at.line}:${at.column}` : "-";
      const pointer = diagnostic.pointer === "" ? "(document)" : diagnostic.pointer;
      return `  ${place} ${pointer}: ${diagnostic.message} [${diagnostic.code}]\n`;
    });
}

/**
 * Check the catalog document `source` the way the app compiles it, then draw each of `sheets` with it; `label`
 * names the document in the text.
 */
export function checkCatalog(source: string, label: string, sheets: readonly Sheet[]): CheckReport {
  const result = compileCatalog(source);
  if (!result.ok) {
    const found = lines(result.diagnostics, source);
    return { ok: false, text: `${label} has ${found.length} problem(s):\n${found.join("")}` };
  }
  const drawn: string[] = [];
  const problems: Diagnostic[] = [];
  for (const sheet of sheets) {
    try {
      const done = exercise(result.catalog, sheet);
      drawn.push(`  ${sheet.name}: ${done.marks} marks\n`);
      problems.push(...done.skipped);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      problems.push({
        severity: "error",
        code: "internal",
        pointer: "",
        message: `${sheet.name}: ${reason}`,
      });
    }
  }
  if (problems.length > 0) {
    return { ok: false, text: `Drawing the examples failed:\n${lines(problems, source).join("")}` };
  }
  return { ok: true, text: `${label}: ${result.catalog.name}\n${drawn.join("")}` };
}
