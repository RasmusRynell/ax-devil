import { describe, expect, it } from "vite-plus/test";
import { outputOf } from "./output";

describe("outputOf", () => {
  it("gives every lane a cell of one size and leaves the unused slots of the last row empty", () => {
    for (const lanes of [1, 2, 3, 4, 5, 6, 7]) {
      const { cells, width, height } = outputOf({ width: 640, height: 360 }, lanes, "Rgba");
      expect(cells.map(({ lane }) => lane)).toEqual(Array.from({ length: lanes }, (_, at) => at));
      expect(new Set(cells.map(({ rect }) => `${rect.width}x${rect.height}`)).size).toBe(1);
      const { width: cellWidth, height: cellHeight } = cells[0].rect;
      const columns = width / cellWidth;
      expect(columns).toBe(lanes <= 2 ? lanes : Math.ceil(Math.sqrt(lanes)));
      expect(height / cellHeight).toBe(Math.ceil(lanes / columns));
      cells.forEach(({ rect }, at) =>
        expect([rect.x, rect.y]).toEqual([
          (at % columns) * cellWidth,
          Math.floor(at / columns) * cellHeight,
        ]),
      );
    }
  });

  it("keeps a single lane at its native size and scales tiled lanes down to fit, never up", () => {
    expect(outputOf({ width: 5000, height: 3000 }, 1, "Rgba")).toMatchObject({
      width: 5000,
      height: 3000,
    });
    const tiled = outputOf({ width: 3840, height: 2160 }, 4, "Rgba");
    expect(tiled).toMatchObject({ width: 3840, height: 2160 });
    expect(tiled.cells[3].rect).toEqual({ x: 1920, y: 1080, width: 1920, height: 1080 });
    expect(outputOf({ width: 100, height: 50 }, 2, "Rgba")).toMatchObject({ width: 200 });
  });

  it("crops a video to even sizes but not a still, leaving the cells whole", () => {
    expect(outputOf({ width: 101, height: 51 }, 1, "I420")).toMatchObject({
      width: 100,
      height: 50,
    });
    const still = outputOf({ width: 101, height: 51 }, 1, "Rgba");
    expect(still).toMatchObject({ width: 101, height: 51 });
  });

  it("sets the lane names at a thirtieth of the cell height, at least the viewer's size", () => {
    expect(outputOf({ width: 640, height: 360 }, 2, "Rgba").nameSize).toBe(12);
    expect(outputOf({ width: 1920, height: 1080 }, 2, "Rgba").nameSize).toBe(36);
    expect(outputOf({ width: 3840, height: 2160 }, 1, "Rgba").nameSize).toBe(72);
  });
});
