import { describe, expect, it } from "vite-plus/test";
import { type Box, elideMiddle, labelRoom, placeChip } from "./chipPlacement";

const LAYER = { width: 400, height: 300 };
const CHIP = { width: 100, height: 30 };

function chipBox(box: Box) {
  const { left, top } = placeChip(box, CHIP, LAYER);
  return { x: left, y: top, ...CHIP };
}

const apart = (a: Box, b: Box) =>
  a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;

describe("hover chip placement", () => {
  const boxes: Box[] = [
    { x: 50, y: 100, width: 80, height: 60 },
    { x: 250, y: 100, width: 140, height: 60 },
    { x: 20, y: 200, width: 360, height: 90 },
    { x: 20, y: 10, width: 360, height: 100 },
    { x: 380, y: 10, width: 20, height: 20 },
    { x: 0, y: 140, width: 10, height: 10 },
    { x: 195, y: 285, width: 10, height: 15 },
    { x: 0, y: 0, width: 400, height: 300 },
  ];

  it("keeps the chip inside the layer and off the hovered box", () => {
    for (const box of boxes) {
      const chip = chipBox(box);
      expect(chip.x).toBeGreaterThanOrEqual(0);
      expect(chip.y).toBeGreaterThanOrEqual(0);
      expect(chip.x + chip.width).toBeLessThanOrEqual(LAYER.width);
      expect(chip.y + chip.height).toBeLessThanOrEqual(LAYER.height);
      // A box that fills the layer leaves no clear side; the chip then covers part of it.
      if (box.width < LAYER.width) expect(apart(chip, box)).toBe(true);
    }
  });

  it("goes to the side with the most free room: right, left, above or below", () => {
    const wide = { x: 20, y: 100, width: 360, height: 60 };
    // Free room by side: right, left, above, below.
    const cases: [Box, "right" | "left" | "above" | "below"][] = [
      [{ x: 50, y: 100, width: 80, height: 60 }, "right"],
      [{ x: 250, y: 100, width: 140, height: 60 }, "left"],
      [{ ...wide, y: 200, height: 90 }, "above"],
      [{ ...wide, y: 10, height: 100 }, "below"],
    ];
    for (const [box, side] of cases) {
      const chip = chipBox(box);
      const placed = {
        right: chip.x >= box.x + box.width,
        left: chip.x + chip.width <= box.x,
        above: chip.y + chip.height <= box.y,
        below: chip.y >= box.y + box.height,
      };
      expect(placed[side]).toBe(true);
    }
  });

  it("prefers right, then left, then above, then below when the room is equal", () => {
    const box = { x: 120, y: 10, width: 160, height: 280 };
    expect(chipBox(box).x).toBeGreaterThanOrEqual(box.x + box.width);
  });
});

describe("hover chip text", () => {
  it("elides the middle of the id and class until it fits, keeping both ends", () => {
    const fits = (text: string) => text.length <= 7;
    const elided = elideMiddle("1234567 truck", fits);
    expect(fits(elided)).toBe(true);
    expect(elided).toContain("…");
    expect(elided.startsWith("123")).toBe(true);
    expect(elided.endsWith("ck")).toBe(true);
    expect(elideMiddle("12 car", fits)).toBe("12 car");
  });

  it("gives the id and class what the facts leave, never less than half and never past the ceiling", () => {
    expect(labelRoom(200, 50, 400)).toBeGreaterThan(labelRoom(200, 120, 400));
    expect(labelRoom(200, 180, 400)).toBeGreaterThanOrEqual(100);
    expect(labelRoom(1000, 0, 300)).toBeLessThanOrEqual(300);
  });
});
