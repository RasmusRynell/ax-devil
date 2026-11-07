// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { makeSample } from "../expr/sampleFixture";
import {
  type ClassMask,
  type MeasureRanges,
  OPEN_RANGES,
  RowSet,
  compileFilter,
  filterRows,
  maskKeeps,
  withRanges,
} from "./filter";

/** Class options as the lane has them: the enabled option indexes out of `options`. */
function choice(options: number, ...enabled: number[]): ClassMask {
  const words = new Uint32Array(Math.ceil(options / 32));
  for (const index of enabled) words[index >> 5] |= 1 << (index & 31);
  return { enabled: words, options };
}

function filtered(expression: string, mask = choice(0)) {
  const compiled = compileFilter(expression);
  if (!compiled.ok) throw new Error(compiled.diagnostics[0].message);
  // A lane without class options has no mask words.
  const words = (...bits: number[]) => (mask.options > 0 ? { mask: bits } : {});
  const sample = makeSample(
    [
      { id: "a", class: "car", score: 0.9, ...words(0b01) },
      { id: "b", class: "car", score: 0.2, ...words(0b01) },
      { id: "c", class: "human", score: 0.8, ...words(0b10) },
      { id: "d", ...words(0) },
    ],
    [
      { kind: "has_part", source: 0, target: 1 },
      { kind: "has_part", source: 0, target: 2 },
      { kind: "has_part", source: 2, target: 3 },
    ],
  );
  const set = filterRows(sample, compiled.filter, mask);
  return {
    rows: Array.from(set.rows.subarray(0, set.count)),
    relations: Array.from(set.relations.subarray(0, set.relationCount)),
  };
}

describe("measure ranges", () => {
  // Rows: a 0.9/0.1, b 0.2/0.5, c 0.5 with no confidence, d reports nothing (no class, no score).
  const sample = makeSample([
    { id: "a", class: "car", score: 0.9, confidence: 0.1 },
    { id: "b", class: "car", score: 0.2, confidence: 0.5 },
    { id: "c", class: "car", score: 0.5 },
    { id: "d" },
  ]);
  const range = (
    measure: "score" | "confidence",
    min: number | null,
    max: number | null,
  ): MeasureRanges => ({ ...OPEN_RANGES, [measure]: { min, max } });

  it.each([
    ["open ends keep everything", "", OPEN_RANGES, [0, 1, 2, 3]],
    ["a minimum", "", range("score", 0.5, null), [0, 2, 3]],
    ["a maximum", "", range("score", null, 0.5), [1, 2, 3]],
    ["inclusive edges", "", range("score", 0.2, 0.9), [0, 1, 2, 3]],
    ["a missing confidence is kept", "", range("confidence", 0.3, null), [1, 2, 3]],
    ["a reversed interval rejects every reported value", "", range("score", 0.9, 0.2), [3]],
    ["a bound too small to print without an exponent", "", range("score", null, 1e-7), [3]],
    ["ranges and the expression both apply", "id != 'c'", range("score", 0.5, null), [0, 3]],
    [
      "each range applies",
      "",
      { score: { min: 0.2, max: null }, confidence: { min: null, max: 0.2 } },
      [0, 2, 3],
    ],
  ])("%s", (_name, expression, ranges, rows) => {
    const compiled = compileFilter(withRanges(expression, ranges));
    if (!compiled.ok) throw new Error(compiled.diagnostics[0].message);
    const set = filterRows(sample, compiled.filter, choice(0));
    expect(Array.from(set.rows.subarray(0, set.count))).toEqual(rows);
  });

  it("leaves the authored expression alone while the ranges are open", () => {
    expect(withRanges("score > 0.4", OPEN_RANGES)).toBe("score > 0.4");
  });
});

describe("class mask", () => {
  // Rows whose classes the decoder lists are kept when one is enabled; unlisted rows stay while something is enabled.
  it.each([
    [[0b01], choice(2, 0), true],
    [[0b10], choice(2, 0), false],
    [[0b11], choice(2, 0), true],
    [[0], choice(2, 0), true],
    [[0], choice(2), false],
    [[0b01], choice(2), false],
    [[0b10], choice(0), true],
    // 70 options span three words: option 69 is bit 5 of word 2, option 33 bit 1 of word 1.
    [[0, 0, 1 << 5], choice(70, 69), true],
    [[0, 0, 1 << 5], choice(70, 0), false],
    [[0, 1 << 1, 0], choice(70, 0), false],
    [[1, 1 << 1, 0], choice(70, 0), true],
    [[0, 1 << 1, 0], choice(70, 0, 33), true],
    [[0, 0, 0], choice(70, 69), true],
    [[0, 0, 0], choice(70), false],
  ])("row mask %j with options %j keeps %s", (rowMask, mask, keeps) => {
    expect(maskKeeps(Uint32Array.from(rowMask), 0, mask)).toBe(keeps);
  });
});

describe("filtering rows", () => {
  it("keeps everything without an expression or class options", () => {
    expect(filtered("").rows).toEqual([0, 1, 2, 3]);
  });

  it("applies the expression after the mask, and a missing value is not kept", () => {
    expect(filtered("score >= 0.5").rows).toEqual([0, 2]);
    expect(filtered("score >= 0.5", choice(2, 0)).rows).toEqual([0]);
  });

  it("keeps a relation only when both ends are kept", () => {
    expect(filtered("score >= 0.5").relations).toEqual([1]);
    expect(filtered("class == 'car'").relations).toEqual([0]);
  });

  it("reuses the row set's arrays from one sample to the next", () => {
    const set = new RowSet();
    const sample = makeSample([{ id: "a" }, { id: "b" }]);
    filterRows(sample, null, choice(0), set);
    const rows = set.rows;
    filterRows(sample, null, choice(0), set);
    expect(set.rows).toBe(rows);
    expect(set.count).toBe(2);
  });

  it("refuses an expression nested too deep to compile, with a diagnostic", () => {
    const result = compileFilter(`${"(".repeat(1000)}true${")".repeat(1000)}`);
    expect(result).toMatchObject({ ok: false, diagnostics: [{ code: "limit" }] });
  });

  it("refuses expressions that do not belong in a filter", () => {
    for (const bad of ["box.w > 1px", "foo", "score +", "score"])
      expect(compileFilter(bad).ok).toBe(false);
  });
});
