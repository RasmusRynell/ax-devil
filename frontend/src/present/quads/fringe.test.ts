import { describe, expect, it } from "vite-plus/test";
import { cornerOffsets } from "./fringe";

/** The offsets of `corners` (x, y pairs) rounded to millionths. */
function offsets(corners: number[]): number[] {
  const out = new Float64Array(corners.length);
  cornerOffsets(corners, corners.length / 2, out);
  return Array.from(out, (v) => Math.round(v * 1e6) / 1e6);
}

describe("cornerOffsets", () => {
  it("moves the edges of a concave polygon outward whichever way it is wound", () => {
    // An L: the reflex corner (2, 2) moves into the notch, which is outside, and the origin corner away from the fill.
    expect(offsets([0, 0, 4, 0, 4, 2, 2, 2, 2, 4, 0, 4])).toEqual([
      -1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1,
    ]);
    // Wound the other way, the same corners keep their offsets.
    expect(offsets([0, 4, 2, 4, 2, 2, 4, 2, 4, 0, 0, 0])).toEqual([
      -1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1,
    ]);
  });
});
