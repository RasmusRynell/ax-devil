// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { fullSample } from "../fixtures";
import { type ClassMask, filterRows } from "../filter/filter";
import { pick } from "./pick";

const view = { width: 1000, height: 500, dpr: 1, zoom: 1 };
const sample = fullSample([
  { id: "big", class: "car", box: [0.1, 0.1, 0.9, 0.9] },
  { id: "small", class: "car", box: [0.4, 0.4, 0.6, 0.6] },
  { id: "twin", class: "car", box: [0.4, 0.4, 0.6, 0.6] },
  { id: "person", class: "human", box: [0.05, 0.05, 0.2, 0.2] },
]);
const all: ClassMask = { enabled: new Uint32Array(sample.maskWords), options: 0 };

describe("picking", () => {
  const kept = filterRows(sample, null, all);

  it("takes the smallest box that contains the point, the first of equal ones, and none outside", () => {
    expect(pick(sample, kept, view, 500, 250)).toBe(1);
    expect(pick(sample, kept, view, 300, 150)).toBe(0);
    expect(pick(sample, kept, view, 100, 50)).toBe(3);
    expect(pick(sample, kept, view, 990, 490)).toBe(-1);
  });

  it("looks at kept rows only: a filtered-out object cannot be picked, and the one under it can", () => {
    const filter = { ctx: null, test: (row: number) => (row === 1 ? 0 : 1) };
    const rows = filterRows(sample, filter, all);
    expect(pick(sample, rows, view, 500, 250)).toBe(2);
    const none = filterRows(sample, { ctx: null, test: () => 0 }, all);
    expect(pick(sample, none, view, 500, 250)).toBe(-1);
  });
});
