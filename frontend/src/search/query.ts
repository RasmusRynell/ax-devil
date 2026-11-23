/**
 * The search box's query language, parsed in the client and sent to the backend as a `SearchWire`. A port of the
 * reference query bar (`scene/query.py`): bare words search free text; `path:value` and `path=value` compare equal;
 * `path>n`, `<`, `>=` and `<=` compare numbers; a leading `-` negates a term; `a OR b` joins adjacent terms;
 * everything else is ANDed. Values may be quoted (`class:"traffic light"`, or `note:""` for an empty value) with
 * `\"` for a literal quote. Parsing never fails: incomplete terms are ignored and a malformed comparison searches as
 * plain text. `fixtures/search/cases.json` holds the groups the reference makes of its texts.
 */

import type { SearchOp, SearchWire, TermWire } from "../generated/api";

/** How a span of the text is used, for highlighting. */
export type SearchTokenKind = "text" | "field" | "operator" | "value" | "or" | "not" | "invalid";

/** A classified span `text.slice(start, end)` of the search; whitespace belongs to no token. */
type SearchToken = { start: number; end: number; kind: SearchTokenKind };

const TERM = /^([A-Za-z_][\p{L}\p{N}_./-]*)(>=|<=|:|=|>|<)/u;
const INTEGER = /^[+-]?[0-9]+$/;
// What Python's float() reads, finite or not, with its digit-group underscores.
const FLOAT =
  /^[+-]?(?:\d+(?:_\d+)*(?:\.(?:\d+(?:_\d+)*)?)?|\.\d+(?:_\d+)*)(?:[eE][+-]?\d+(?:_\d+)*)?$/;
const SPACE = /\s/;
const OPS: Record<string, SearchOp> = {
  ":": "eq",
  "=": "eq",
  ">": "gt",
  "<": "lt",
  ">=": "ge",
  "<=": "le",
};

/**
 * The number `value` reads as, as the reference decides (digit-group underscores allowed); null when it is none. A whole
 * number is a `bigint` so that it stays exact beyond 2^53; JavaScript compares a `bigint` with a number by value.
 */
export function numberOf(value: string): number | bigint | null {
  if (INTEGER.test(value)) return BigInt(value);
  const trimmed = value.trim();
  if (!FLOAT.test(trimmed)) return null;
  const number = Number(trimmed.replaceAll("_", ""));
  return Number.isFinite(number) ? number : null;
}

/** Drops the delimiting double quotes of a value; `\"` becomes a literal quote. */
const unquote = (text: string): string =>
  text.replace(/\\"|"/g, (found) => (found.length === 2 ? '"' : ""));

/** The (start, end) of each whitespace-separated word; whitespace inside double quotes is kept. */
function words(text: string): [number, number][] {
  const found: [number, number][] = [];
  let start: number | null = null;
  let quoted = false;
  let escaped = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    const wasEscaped = escaped;
    escaped = char === "\\";
    if (SPACE.test(char) && !quoted) {
      if (start !== null) found.push([start, index]);
      start = null;
      continue;
    }
    start ??= index;
    if (char === '"' && !wasEscaped) quoted = !quoted;
  }
  if (start !== null) found.push([start, text.length]);
  return found;
}

type Word = { tokens: SearchToken[]; term: TermWire | null; or: boolean };

/** Classifies one word; the term is null when the word is incomplete and ignored. */
function parseWord(text: string, from: number, end: number): Word {
  const tokens: SearchToken[] = [];
  let start = from;
  const negated = end - start > 1 && text[start] === "-";
  if (negated) {
    tokens.push({ start, end: start + 1, kind: "not" });
    start++;
  }
  const rest = text.slice(start, end);
  const invalid = (term: TermWire | null): Word => ({
    tokens: [...tokens, { start, end, kind: "invalid" }],
    term,
    or: false,
  });
  const found = TERM.exec(rest);
  if (found === null) {
    const value = unquote(rest);
    if (value === "" || value === "-") return invalid(null);
    return {
      tokens: [...tokens, { start, end, kind: "text" }],
      term: { path: null, op: "eq", value, negated },
      or: false,
    };
  }
  const [matched, path, op] = found;
  const raw = rest.slice(matched.length);
  const value = unquote(raw);
  // An explicit `""` asks for the empty value; a bare or half-typed value is still being typed.
  if (value === "" && raw !== '""') return invalid(null);
  if (op !== ":" && op !== "=" && numberOf(value) === null)
    return invalid({ path: null, op: "eq", value: unquote(rest), negated });
  const fieldEnd = start + path.length;
  const opEnd = start + matched.length;
  tokens.push(
    { start, end: fieldEnd, kind: "field" },
    { start: fieldEnd, end: opEnd, kind: "operator" },
    { start: opEnd, end, kind: "value" },
  );
  return { tokens, term: { path, op: OPS[op], value, negated }, or: false };
}

/** Parses the search text into the terms to send and the tokens to highlight. */
export function parseSearch(text: string): { wire: SearchWire; tokens: SearchToken[] } {
  const items = words(text).map(([start, end]): Word =>
    text.slice(start, end) === "OR"
      ? { tokens: [{ start, end, kind: "or" }], term: null, or: true }
      : parseWord(text, start, end),
  );
  const tokens: SearchToken[] = [];
  const groups: TermWire[][] = [];
  let join = false;
  items.forEach((item, index) => {
    if (item.or) {
      // OR only joins when a term stands on both sides.
      join = (items[index - 1]?.term ?? null) !== null && (items[index + 1]?.term ?? null) !== null;
      const [only] = item.tokens;
      tokens.push(join ? only : { ...only, kind: "invalid" });
      return;
    }
    if (item.term !== null) {
      if (join && groups.length > 0) groups[groups.length - 1].push(item.term);
      else groups.push([item.term]);
      join = false;
    }
    tokens.push(...item.tokens);
  });
  return { wire: { groups }, tokens };
}
