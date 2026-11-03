import { describe, expect, it } from "vite-plus/test";
import { elideMiddle, labelRoom, placeChip } from "./chipPlacement";

const LAYER = { width: 400, height: 300 };
const CHIP = { width: 100, height: 30 };

describe("hover chip placement", () => {
  it("goes beside the box with the most free room: right, left, above or below", () => {
    // Beside the top-right corner, a gap away.
    expect(placeChip({ x: 50, y: 100, width: 80, height: 60 }, CHIP, LAYER)).toEqual({
      left: 138,
      top: 100,
    });
    expect(placeChip({ x: 250, y: 100, width: 140, height: 60 }, CHIP, LAYER)).toEqual({
      left: 142,
      top: 100,
    });
    // Above, its right edge on the box's.
    expect(placeChip({ x: 20, y: 200, width: 360, height: 90 }, CHIP, LAYER)).toEqual({
      left: 280,
      top: 162,
    });
    expect(placeChip({ x: 20, y: 10, width: 360, height: 100 }, CHIP, LAYER)).toEqual({
      left: 280,
      top: 118,
    });
  });

  it("prefers right, then left, then above, then below when the room is equal", () => {
    expect(placeChip({ x: 120, y: 10, width: 160, height: 280 }, CHIP, LAYER)).toEqual({
      left: 288,
      top: 10,
    });
  });

  it("stays inside the layer", () => {
    expect(placeChip({ x: 380, y: 10, width: 20, height: 20 }, CHIP, LAYER)).toEqual({
      left: 272,
      top: 10,
    });
  });

  it("covers part of a box that fills the layer, by its top-right corner", () => {
    expect(placeChip({ x: 0, y: 0, width: 400, height: 300 }, CHIP, LAYER)).toEqual({
      left: 292,
      top: 8,
    });
  });
});

describe("hover chip text", () => {
  it("elides the middle of the id and class until it fits, keeping both ends", () => {
    const fits = (text: string) => text.length <= 7;
    expect(elideMiddle("1234 car", fits)).toBe("123…car");
    expect(elideMiddle("12 car", fits)).toBe("12 car");
  });

  it("gives the id and class what the facts leave, never less than half and never past the ceiling", () => {
    expect(labelRoom(200, 50, 400)).toBe(142);
    expect(labelRoom(200, 180, 400)).toBe(100);
    expect(labelRoom(1000, 0, 300)).toBe(300);
  });
});
