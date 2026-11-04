import { describe, expect, it } from "vite-plus/test";
import { seekBand } from "./seekBand";

describe("seek band", () => {
  it("gives a single cached frame one frame's width and a trailing run the rail's end", () => {
    expect(seekBand(25, 25, 100)).toEqual({ from: 0.25, span: 0.01 });
    const trailing = seekBand(90, 99, 100);
    expect(trailing.from + trailing.span).toBeCloseTo(1);
  });

  it("covers nothing without frames", () => {
    expect(seekBand(0, 0, 0).span).toBe(0);
  });
});
