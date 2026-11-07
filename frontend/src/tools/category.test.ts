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
      expect(categoryDot(name)).toBe(`bg-category-${describeRow(sample, row).category}`);
    });
    expect(categoryDot("TRUCK")).toBe(categoryDot("truck"));
  });
});
