import { compileCatalog } from "./compile";
import { locate } from "./locate";

/** What `catalog check` says about a document. */
export interface CheckReport {
  readonly ok: boolean;
  readonly text: string;
}

/** Check the catalog document `source` the way the app compiles it; `label` names it in the text. */
export function checkCatalog(source: string, label: string): CheckReport {
  const result = compileCatalog(source);
  if (result.ok) {
    return { ok: true, text: `${label}: ${result.catalog.name}\n` };
  }
  const found = result.diagnostics
    .map((diagnostic) => ({ diagnostic, at: locate(diagnostic, source) }))
    .sort(
      (a, b) => (a.at?.line ?? 0) - (b.at?.line ?? 0) || (a.at?.column ?? 0) - (b.at?.column ?? 0),
    );
  const lines = found.map(({ diagnostic, at }) => {
    const place = at ? `${at.line}:${at.column}` : "-";
    const pointer = diagnostic.pointer === "" ? "(document)" : diagnostic.pointer;
    return `  ${place} ${pointer}: ${diagnostic.message} [${diagnostic.code}]\n`;
  });
  return { ok: false, text: `${label} has ${lines.length} problem(s):\n${lines.join("")}` };
}
