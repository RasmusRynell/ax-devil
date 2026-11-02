/**
 * Which records a parsed search keeps, for the lists the client itself holds (Frame scope); File scope's backend judges
 * the same way over text. Every group must have a term that holds; a term holds when the record matches it, unless
 * negated.
 */

import type { SearchWire, TermWire } from "../generated/api";
import { numberOf } from "./query";

/** What a search looks at in one object: values by dotted field path, and the strings free text searches. */
export type SearchRecord = {
  fields: { readonly [path: string]: readonly (string | number)[] };
  text: readonly string[];
};

/** Whether `path` names `key`: it equals the key or a trailing run of its dotted parts, ignoring case. */
const names = (path: string, key: string): boolean => {
  const name = key.toLowerCase();
  return name === path || name.endsWith(`.${path}`);
};

/** Text equals ignoring case and is never greater or less than anything; a number compares by value. */
function compares(term: TermWire, value: string | number): boolean {
  if (typeof value === "string")
    return term.op === "eq" && value.toLowerCase() === term.value.toLowerCase();
  const wanted = numberOf(term.value);
  if (wanted === null) return false;
  switch (term.op) {
    case "eq":
      return value === wanted;
    case "gt":
      return value > wanted;
    case "lt":
      return value < wanted;
    case "ge":
      return value >= wanted;
    case "le":
      return value <= wanted;
  }
}

function holds(term: TermWire, record: SearchRecord): boolean {
  const hit =
    term.path === null
      ? record.text.some((text) => text.toLowerCase().includes(term.value.toLowerCase()))
      : Object.entries(record.fields).some(
          ([key, values]) =>
            names(term.path!.toLowerCase(), key) && values.some((v) => compares(term, v)),
        );
  return hit !== term.negated;
}

/** Whether `record` passes the search; a search with no groups keeps everything. */
export const matches = (search: SearchWire, record: SearchRecord): boolean =>
  search.groups.every((group) => group.some((term) => holds(term, record)));
