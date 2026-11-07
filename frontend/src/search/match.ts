/**
 * Which records a parsed search keeps, for the lists the client itself holds (Frame scope); File scope's backend judges
 * the same way over text. Every group must have a term that holds; a term holds when the record matches it, unless
 * negated.
 */

import type { SearchWire, TermWire } from "../generated/api";
import { numberOf } from "./query";

/** What a search looks at in one object: values by dotted field path, and the strings free text searches. */
export type SearchRecord = {
  fields: { readonly [path: string]: readonly (string | number | bigint)[] };
  text: readonly string[];
};

/** Whether `path` names `key`: it equals the key or a trailing run of its dotted parts, ignoring case. */
export const names = (path: string, key: string): boolean => {
  const name = key.toLowerCase();
  return name === path || name.endsWith(`.${path}`);
};

/**
 * Text equals ignoring case and is never greater or less than anything; a number compares by value, a whole number
 * exactly even beside a float. `wanted` is the term's value as a number, if it reads as one.
 */
function compares(term: TermWire, wanted: number | bigint | null, value: string | number | bigint) {
  if (typeof value === "string")
    return term.op === "eq" && value.toLowerCase() === term.value.toLowerCase();
  if (wanted === null) return false;
  switch (term.op) {
    case "eq":
      return !(value < wanted) && !(value > wanted);
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
  const needle = term.value.toLowerCase();
  const path = term.path?.toLowerCase();
  const wanted = path === undefined ? null : numberOf(term.value);
  const hit =
    path === undefined
      ? record.text.some((text) => text.toLowerCase().includes(needle))
      : Object.entries(record.fields).some(
          ([key, values]) => names(path, key) && values.some((v) => compares(term, wanted, v)),
        );
  return hit !== term.negated;
}

/** Whether `record` passes the search; a search with no groups keeps everything. */
export const matches = (search: SearchWire, record: SearchRecord): boolean =>
  search.groups.every((group) => group.some((term) => holds(term, record)));

/**
 * The field paths the search names that no record in `records` has, in search order without repeats; none when
 * `records` is empty. A path counts as present when some record has a key it names, whatever the key's values.
 */
export function missingPaths(search: SearchWire, records: readonly SearchRecord[]): string[] {
  if (records.length === 0) return [];
  const keys = records.flatMap((record) => Object.keys(record.fields));
  const paths = search.groups.flat().flatMap((term) => (term.path === null ? [] : [term.path]));
  return [...new Set(paths)].filter((path) => !keys.some((key) => names(path.toLowerCase(), key)));
}
