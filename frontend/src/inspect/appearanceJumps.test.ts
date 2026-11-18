// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { jumpsFrom } from "./appearanceJumps";

// Frames 2..4, a one-frame gap at 5, 6..6, then 20..25.
const SPANS = [
  [2, 4],
  [6, 6],
  [20, 25],
] as const;

describe("jumpsFrom", () => {
  it("goes to the first span before the first and the last frame after the last", () => {
    expect(jumpsFrom(SPANS, 0)).toEqual({ last: null, next: 2 });
    expect(jumpsFrom(SPANS, 30)).toEqual({ last: 25, next: null });
  });

  it("keeps a gap of one frame: the frame in it has both sides", () => {
    expect(jumpsFrom(SPANS, 5)).toEqual({ last: 4, next: 6 });
    expect(jumpsFrom(SPANS, 10)).toEqual({ last: 6, next: 20 });
  });

  it("offers nothing inside a span, at either end of it", () => {
    for (const frame of [2, 3, 4, 6, 20, 25])
      expect(jumpsFrom(SPANS, frame)).toEqual({ last: null, next: null });
  });

  it("does not guess past the spans it was given", () => {
    expect(jumpsFrom(SPANS, 30, true)).toEqual({ last: null, next: null });
    expect(jumpsFrom(SPANS, 10, true)).toEqual({ last: 6, next: 20 });
  });

  it("offers nothing for an object that is never shown", () => {
    expect(jumpsFrom([], 3)).toEqual({ last: null, next: null });
  });
});
