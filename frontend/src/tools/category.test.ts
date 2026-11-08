import { describe, expect, it } from "vite-plus/test";
import { describeRow } from "../playback/laneLayouts";
import { fullSample } from "../present/fixtures";
import { categoryDot } from "./category";

describe("categoryDot", () => {
  it("gives a class the color the hover chip gives it, whatever its case", () => {
    const names = ["car", "human", "Truck"];
    const sample = fullSample(
      names.map((name, i) => ({ id: String(i), class: name, box: [0, 0, 1, 1] })),
    );
    names.forEach((name, row) => {
      expect(categoryDot(name)).toBe(`bg-dot-${describeRow(sample, row).category}`);
    });
    expect(categoryDot("TRUCK")).toBe(categoryDot("truck"));
    // Case folding, not lower-casing: `ß` is `ss`, and a final `ς` is `σ`.
    expect(categoryDot("Straße")).toBe(categoryDot("STRASSE"));
    expect(categoryDot("ΣΑΣ")).toBe(categoryDot("σας"));
    expect(categoryDot("ẞperson")).toBe(categoryDot("SSPERSON"));
    // The dotless `ı` is its own letter, and Cherokee folds to capitals, as the reference's `casefold` has it.
    expect(categoryDot("ı")).not.toBe(categoryDot("i"));
    expect(categoryDot("ꭰ")).toBe(categoryDot("Ꭰ"));
  });
});
