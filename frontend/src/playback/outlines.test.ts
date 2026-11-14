import { describe, expect, it } from "vite-plus/test";
import { reaches } from "./outlines";

const CELL = { x: 0, y: 0, width: 100, height: 100 };

describe("outline reach", () => {
  it("keeps an outline whose stroke still shows after its contour has left the cell", () => {
    const outside = { x: 101, y: 10, width: 20, height: 20 };
    // A 4px stroke centered on the contour reaches 2px in: 1px of it shows.
    expect(reaches(outside, CELL, 4)).toBe(true);
    expect(reaches(outside, CELL, 1)).toBe(false);
    expect(reaches({ x: 110, y: 10, width: 20, height: 20 }, CELL, 4)).toBe(false);
  });

  it("omits a box without area", () => {
    expect(reaches({ x: 10, y: 10, width: 0, height: 20 }, CELL, 4)).toBe(false);
  });
});
