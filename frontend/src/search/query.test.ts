// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import cases from "../../../fixtures/search/cases.json";
import type { SearchOp, SearchWire, TermWire } from "../generated/api";
import { matches, type SearchRecord } from "./match";
import { parseSearch } from "./query";

const records: { [name: string]: SearchRecord } = cases.records;

const OPS: { [op: string]: SearchOp } = { Eq: "eq", Gt: "gt", Lt: "lt", Ge: "ge", Le: "le" };

/** The fixture's groups in the wire's spelling (the reference names its operators `Eq`, `Gt`...). */
const wireOf = (groups: (typeof cases.cases)[number]["groups"]): SearchWire => ({
  groups: groups.map((group) =>
    group.map((term) => ({ ...term, op: OPS[term.op] }) satisfies TermWire),
  ),
});

describe("the shared search fixture", () => {
  it.each(cases.cases)("reads $text as the reference does", ({ text, groups, matches: names }) => {
    const { wire } = parseSearch(text);
    expect(wire).toEqual(wireOf(groups));
    const found = Object.keys(records).filter((name) => matches(wire, records[name]));
    expect(found).toEqual(names);
  });
});

describe("numbers with digit-group underscores", () => {
  const record: SearchRecord = { fields: { score: [0.9] }, text: [] };
  it.each([
    ["score>0.1_0", true],
    ["-score>0.1_0", false],
    ["score>0.9_5", false],
    ["-score>0.9_5", true],
  ])("%s keeps the record: %s", (text, kept) => {
    expect(matches(parseSearch(text).wire, record)).toBe(kept);
  });
});

describe("tokens", () => {
  /** The text of each token with its kind, so a test reads like the search box. */
  const spans = (text: string) =>
    parseSearch(text).tokens.map(({ start, end, kind }) => `${kind}:${text.slice(start, end)}`);

  it("marks the parts of a term and the words that join or negate", () => {
    expect(spans("-class:car OR id>=3 bus")).toEqual([
      "not:-",
      "field:class",
      "operator::",
      "value:car",
      "or:OR",
      "field:id",
      "operator:>=",
      "value:3",
      "text:bus",
    ]);
  });

  it("marks what is ignored or searched as plain text as invalid", () => {
    expect(spans('OR car id: score>abc "')).toEqual([
      "invalid:OR",
      "text:car",
      "invalid:id:",
      "invalid:score>abc",
      'invalid:"',
    ]);
  });

  it("keeps whitespace inside quotes in one value", () => {
    expect(spans('class:"traffic light" x')).toEqual([
      "field:class",
      "operator::",
      'value:"traffic light"',
      "text:x",
    ]);
  });
});
