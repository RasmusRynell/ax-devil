// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { OverlayBuffer } from "./OverlayBuffer";

const drawing = (generation = 0) => ({ generation });

describe("overlay buffer", () => {
  it("accepts nothing before a window, then keeps exactly the window's frames per lane", () => {
    const buffer = new OverlayBuffer<{ generation: number }>(20);
    expect(buffer.add(0, 5, 0, drawing())).toBe(false);
    buffer.setWindow({ playhead: 5, ahead: 2, behind: 1 });
    for (let id = 0; id < 20; id++) buffer.add(0, id, 0, drawing());
    buffer.add(1, 5, 0, drawing());
    expect([4, 5, 6, 7, 8].map((id) => buffer.has(0, id))).toEqual([true, true, true, true, false]);
    expect(buffer.has(1, 5)).toBe(true);

    buffer.setWindow({ playhead: 7, ahead: 1, behind: 1 });
    expect([4, 5, 6, 7].map((id) => buffer.has(0, id))).toEqual([false, false, true, true]);
    expect(buffer.has(1, 5)).toBe(false);
  });

  it("lets the newest revision replace an older one and ignores an older one that arrives later", () => {
    const buffer = new OverlayBuffer<{ generation: number }>(20);
    buffer.setWindow({ playhead: 5, ahead: 2, behind: 1 });
    const first = drawing();
    const second = drawing();
    expect(buffer.add(0, 5, 1, first)).toBe(true);
    expect(buffer.add(0, 5, 0, drawing())).toBe(false);
    expect(buffer.get(0, 5)).toBe(first);
    expect(buffer.add(0, 5, 2, second)).toBe(true);
    expect(buffer.get(0, 5)).toBe(second);
  });

  it("replaces a drawing with the same revision prepared on a newer atlas page, never on an older one", () => {
    const buffer = new OverlayBuffer<{ generation: number }>(20);
    buffer.setWindow({ playhead: 5, ahead: 2, behind: 1 });
    const again = drawing(2);
    buffer.add(0, 5, 3, drawing(1));
    expect(buffer.add(0, 5, 3, again)).toBe(true);
    expect(buffer.get(0, 5)).toBe(again);
    expect(buffer.add(0, 5, 3, drawing(1))).toBe(false);
  });

  it("forgets a lane", () => {
    const buffer = new OverlayBuffer<{ generation: number }>(20);
    buffer.setWindow({ playhead: 5, ahead: 2, behind: 1 });
    buffer.add(0, 5, 0, drawing());
    buffer.add(1, 5, 0, drawing());
    buffer.forgetLane(0);
    expect([buffer.has(0, 5), buffer.has(1, 5)]).toEqual([false, true]);
  });
});
