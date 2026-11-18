import type { CompiledCatalog } from "./types";

/** The index of a theme for `computeVars`: 0 for none or a name the catalog does not have. */
export function themeIndex(catalog: CompiledCatalog, theme: string | null): number {
  return theme === null ? 0 : catalog.themes.indexOf(theme) + 1;
}

/**
 * Work out the variables of one row into the context's registers: every rule's condition once, then each variable
 * from the last matching rule that sets it, else its default (the theme's, if the theme gives one). The context must
 * be bound to the sample.
 */
export function computeVars(catalog: CompiledCatalog, row: number, theme: number): void {
  const hit = catalog.ctx.ruleHit;
  const rules = catalog.rules;
  for (let i = 0; i < rules.length; i++) hit[i] = rules[i].fn(row) === 1 ? 1 : 0;
  for (const variable of catalog.vars) {
    const assignments = variable.assignments;
    let set = variable.defaults[theme];
    for (let i = assignments.length - 1; i >= 0; i--) {
      if (hit[assignments[i].rule] === 1) {
        set = assignments[i].set;
        break;
      }
    }
    set(row);
  }
}
