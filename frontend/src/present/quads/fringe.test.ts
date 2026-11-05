import { describe, expect, it } from "vite-plus/test";
import { CORNER_FLOATS, cornerOffsets } from "./fringe";
import { FillWriter, FRINGE_BUDGET } from "./lane";
import { FILL_HEADER_FLOATS, FILL_VERTEX_FLOATS } from "../../overlay/quads";

/** The offsets of `corners` (x, y pairs), as x, y and room per corner, rounded to millionths. */
function offsets(corners: number[]): number[] {
  const out = new Float64Array((corners.length / 2) * CORNER_FLOATS);
  cornerOffsets(corners, corners.length / 2, out);
  return Array.from(out, (v) => Math.round(v * 1e6) / 1e6);
}

/** Whether any two edges of the closed polygon `xy` that do not share a corner cross. */
function crosses(xy: number[]): boolean {
  const count = xy.length / 2;
  const side = (a: number, b: number, c: number) =>
    Math.sign(
      (xy[b * 2] - xy[a * 2]) * (xy[c * 2 + 1] - xy[a * 2 + 1]) -
        (xy[b * 2 + 1] - xy[a * 2 + 1]) * (xy[c * 2] - xy[a * 2]),
    );
  for (let a = 0; a < count; a++)
    for (let b = a + 2; b < count; b++) {
      if (a === 0 && b === count - 1) continue;
      const [a2, b2] = [(a + 1) % count, (b + 1) % count];
      if (side(a, a2, b) !== side(a, a2, b2) && side(b, b2, a) !== side(b, b2, a2)) return true;
    }
  return false;
}

describe("cornerOffsets", () => {
  it("moves the edges of a concave polygon outward whichever way it is wound", () => {
    // An L: the reflex corner (2, 2) moves into the notch, which is outside, and the origin corner away from the fill.
    const xy = (list: number[]) => offsets(list).filter((_, at) => at % CORNER_FLOATS !== 2);
    expect(xy([0, 0, 4, 0, 4, 2, 2, 2, 2, 4, 0, 4])).toEqual([
      -1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1,
    ]);
    // Wound the other way, the same corners keep their offsets.
    expect(xy([0, 4, 2, 4, 2, 2, 4, 2, 4, 0, 0, 0])).toEqual([
      -1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1,
    ]);
  });

  it("keeps a narrow bridge between two big regions from inverting when its edges move in", () => {
    // Two 10x10 squares joined by a bridge 0.2 thick.
    const bridge = [
      0, 0, 10, 0, 10, 5.05, 20, 5.05, 20, 0, 30, 0, 30, 10, 20, 10, 20, 5.25, 10, 5.25, 10, 10, 0,
      10,
    ];
    const out = new Float64Array((bridge.length / 2) * CORNER_FLOATS);
    cornerOffsets(bridge, bridge.length / 2, out);
    const moved = (inset: (corner: number) => number) =>
      bridge.map((v, at) => {
        const corner = Math.floor(at / 2);
        return v - out[corner * CORNER_FLOATS + (at % 2)] * inset(corner);
      });
    const fringe = 0.5;
    // Every corner on the bridge may move in by less than half its thickness, and the rest by the whole fringe.
    for (const corner of [2, 3, 8, 9])
      expect(out[corner * CORNER_FLOATS + 2]).toBeLessThanOrEqual(0.1);
    for (const corner of [0, 5, 6, 11])
      expect(out[corner * CORNER_FLOATS + 2]).toBeGreaterThan(fringe);
    expect(crosses(moved((corner) => Math.min(fringe, out[corner * CORNER_FLOATS + 2])))).toBe(
      false,
    );
    // Without the room the moved bridge turns inside out, as it did when only the whole polygon's thickness counted.
    expect(crosses(moved(() => fringe))).toBe(true);
  });
});

describe("FillWriter", () => {
  /** A circle of `count` corners filling most of the picture, as fractions of it. */
  const circle = (count: number) => {
    const points = new Float32Array(count * 2);
    for (let at = 0; at < count; at++) {
      points[at * 2] = 0.5 + 0.4 * Math.cos((at / count) * 2 * Math.PI);
      points[at * 2 + 1] = 0.5 + 0.4 * Math.sin((at / count) * 2 * Math.PI);
    }
    return points;
  };

  /** The offset vectors of the first corner of each block written. */
  function firstOffsets(fills: FillWriter, counts: number[]): number[][] {
    const view = fills.view();
    let at = 0;
    return counts.map((count) => {
      const vertex = at + FILL_HEADER_FLOATS;
      at += FILL_HEADER_FLOATS + (3 * count + 2) * FILL_VERTEX_FLOATS;
      return [view[vertex + 2], view[vertex + 3]];
    });
  }

  it("gives hard edges to the polygons past a layout's budget, and a new layout a new budget", () => {
    // Two polygons that fit the budget one at a time, then a small one that still fits what is left of it.
    const big = Math.floor(Math.sqrt(0.6 * FRINGE_BUDGET));
    const fills = new FillWriter();
    const layout = () => {
      fills.clear();
      for (const count of [big, big, 8]) fills.add(circle(count), 0, count, 100, 100, 1);
      return firstOffsets(fills, [big, big, 8]);
    };
    const [first, second, small] = layout();
    expect(Math.hypot(...first)).toBeGreaterThan(0.5);
    expect(second).toEqual([0, 0]);
    expect(Math.hypot(...small)).toBeGreaterThan(0.5);
    expect(layout()).toEqual([first, second, small]);
  });
});
