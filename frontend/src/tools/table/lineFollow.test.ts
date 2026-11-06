// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { lineInView, scrollForLine } from "./lineFollow";

/** A view 200 tall of rows 20 tall, scrolled to `top`, over 1000 rows. */
const view = (top: number) => ({ top, height: 200, row: 20, rows: 1000 });

describe("following the line", () => {
  it("keeps the line in view while the row above it, whole, is", () => {
    // Line before row 30: the row above it is row 29, at 580.
    expect(lineInView(30, view(400))).toBe(true);
    expect(lineInView(30, view(580))).toBe(true);
    expect(lineInView(30, view(399))).toBe(false);
    expect(lineInView(30, view(581))).toBe(false);
    expect(lineInView(30, view(0))).toBe(false);
    // Nothing has happened yet: the first row is the one.
    expect(lineInView(0, view(0))).toBe(true);
  });

  it("scrolls only once the line has left the view, to a third of the way down", () => {
    expect(scrollForLine({ line: 30, keep: "third" }, view(500))).toBeNull();
    expect(scrollForLine({ line: 30, keep: "third" }, view(0))).toBe(580 - 66);
    expect(scrollForLine({ line: 2, keep: "third" }, view(500))).toBe(0);
  });

  it("keeps the end of a list that grows in view, and knows nothing without a line or rows", () => {
    expect(scrollForLine({ line: 1000, keep: "end" }, view(0))).toBe(1000 * 20 - 200);
    expect(scrollForLine({ line: 1000, keep: "end" }, view(19800))).toBeNull();
    expect(scrollForLine({ line: null, keep: "third" }, view(0))).toBeNull();
    expect(scrollForLine({ line: 30, keep: "third" }, { ...view(0), row: 0 })).toBeNull();
  });
});
