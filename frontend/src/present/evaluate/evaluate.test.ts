// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { compileCatalogDocument } from "../catalog/compile";
import type { CompiledCatalog } from "../catalog/types";
import { builtIn, hiddenBits } from "../canonical/render";
import { RowSet, filterRows } from "../filter/filter";
import { fullSample } from "../fixtures";
import { Rec } from "../marks/buffer";
import { MarkBuffer } from "../marks/buffer";
import { evaluate } from "./evaluate";
import { EvalReport } from "./report";

function compile(layers: unknown[], vars: Record<string, unknown> = {}): CompiledCatalog {
  const result = compileCatalogDocument({
    schema: 4,
    id: "t",
    name: "t",
    description: "t",
    vars,
    layers,
  });
  if (!result.ok)
    throw new Error(result.diagnostics.map((d) => `${d.pointer}: ${d.message}`).join("; "));
  return result.catalog;
}

function run(catalog: CompiledCatalog, sample: ReturnType<typeof fullSample>, hidden = 0) {
  const set = filterRows(
    sample,
    null,
    { enabled: new Uint32Array(sample.maskWords), options: 0 },
    new RowSet(),
  );
  const marks = new MarkBuffer();
  const report = new EvalReport();
  evaluate(sample, set, catalog, { hidden, theme: null }, marks, report);
  return { marks, report };
}

const place = [{ at: "top-left", anchor: "bottom-left" }];

describe("evaluate", () => {
  it("leaves out the layers of hidden features and keeps layer order", () => {
    const sample = fullSample([
      { id: "a", class: "car", score: 0.5 },
      { id: "b", class: "human", score: 0.7 },
    ]);
    const all = run(builtIn("standard"), sample).marks;
    const hidden = run(builtIn("standard"), sample, hiddenBits(["ids", "score"])).marks;
    expect(all.count).toBeGreaterThan(hidden.count);
    expect(hidden.layerCount).toBe(all.layerCount);
    for (const marks of [all, hidden])
      for (let layer = 1; layer < marks.layerCount; layer++)
        expect(marks.layerEnd[layer]).toBeGreaterThanOrEqual(marks.layerEnd[layer - 1]);
  });

  it("skips and counts a mark whose needed field is null, without touching the rest", () => {
    // The text mark needs a colour that only some rows have; the box beside it does not.
    const catalog = compile([
      {
        marks: [
          { mark: "box", stroke: "#ffffff", width: "1px" },
          { mark: "text", text: "id", color: "top(attr.tint)", size: "10px", place },
        ],
      },
    ]);
    const sample = fullSample([
      { id: "a", attrs: { tint: [{ rgba: 0xff0000ff, score: 1 }] } },
      { id: "b", attrs: { tint: [] } },
      { id: "c" },
    ]);
    const { marks, report } = run(catalog, sample);
    expect(marks.count).toBe(4);
    expect(Array.from(report.skipped)).toEqual([0, 2]);
    expect(report.total()).toBe(2);
  });

  it("drops a label with no visible run silently and skips a null run", () => {
    const catalog = compile([
      {
        marks: [
          {
            mark: "label",
            size: "10px",
            runs: [
              { text: "fixed(score, 2)", color: "#ffffff" },
              { text: "fixed(confidence, 2)", color: "#ffffff" },
            ],
            place,
          },
        ],
      },
    ]);
    const sample = fullSample([
      { id: "a", score: 0.5, confidence: 0.25 },
      { id: "b", score: 0.5 },
      { id: "c" },
    ]);
    const { marks, report } = run(catalog, sample);
    expect(marks.count).toBe(2);
    expect([marks.words[Rec.B], marks.words[12 + Rec.B]]).toEqual([2, 1]);
    expect(report.total()).toBe(0);
  });

  it("is reused from sample to sample and grows with the rows", () => {
    const catalog = builtIn("classic");
    const marks = new MarkBuffer();
    const report = new EvalReport();
    for (const rows of [3, 500, 3]) {
      const sample = fullSample(
        Array.from({ length: rows }, (_, i) => ({ id: `t-${i}`, class: "car", score: 0.5 })),
      );
      const set = filterRows(
        sample,
        null,
        { enabled: new Uint32Array(0), options: 0 },
        new RowSet(),
      );
      evaluate(sample, set, catalog, { hidden: 0, theme: null }, marks, report);
      expect(marks.count).toBe(marks.layerEnd[marks.layerCount - 1]);
      expect(marks.count / rows).toBeGreaterThanOrEqual(3);
    }
    expect(marks.count).toBeLessThan(60);
  });
});
