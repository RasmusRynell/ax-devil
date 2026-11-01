import { describe, expect, it } from "vite-plus/test";
import { placeChip } from "./chipPlacement";

const LAYER = { width: 400, height: 300 };
const CHIP = { width: 100, height: 30 };

describe("hover chip placement", () => {
  it("goes just below the box, left edges aligned, so it never covers the id label above it", () => {
    expect(placeChip({ x: 50, y: 100, width: 80, height: 60 }, CHIP, LAYER)).toEqual({
      left: 50,
      top: 160,
    });
  });

  it("keeps inside the layer's width", () => {
    expect(placeChip({ x: 380, y: 10, width: 20, height: 20 }, CHIP, LAYER).left).toBe(300);
  });

  it("goes beside the box when there is no room below, to the right first and then the left", () => {
    expect(placeChip({ x: 50, y: 100, width: 80, height: 190 }, CHIP, LAYER)).toEqual({
      left: 130,
      top: 100,
    });
    expect(placeChip({ x: 250, y: 100, width: 140, height: 190 }, CHIP, LAYER)).toEqual({
      left: 150,
      top: 100,
    });
  });

  it("goes inside the box's bottom edge when the box fills the layer's width and height", () => {
    expect(placeChip({ x: 0, y: 0, width: 400, height: 300 }, CHIP, LAYER)).toEqual({
      left: 0,
      top: 270,
    });
  });
});
