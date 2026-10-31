// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { FrameBuffer, windowSize } from "./FrameBuffer";

const frame = (byteLength: number) => ({ byteLength });
const held = (buffer: FrameBuffer<{ byteLength: number }>, count: number) =>
  Array.from({ length: count }, (_, id) => id).filter((id) => buffer.has(id));

describe("window size", () => {
  it.each([
    { budget: 1000, frame: 300, size: 3 },
    { budget: 900, frame: 300, size: 3 },
    { budget: 100, frame: 300, size: 1 },
  ])(
    "is $size frames for a $budget byte budget of $frame byte frames",
    ({ budget, frame, size }) => {
      expect(windowSize(budget, frame)).toBe(size);
    },
  );
});

describe("frame buffer (B30)", () => {
  it("keeps exactly the frames inside the reported window, within the byte budget", () => {
    const buffer = new FrameBuffer<{ byteLength: number }>(500, 20);
    expect(buffer.add(5, frame(100))).toBe(false);
    buffer.setWindow({ playhead: 5, ahead: 3, behind: 1 });
    for (let id = 0; id < 20; id++) buffer.add(id, frame(100));
    expect(held(buffer, 20)).toEqual([4, 5, 6, 7, 8]);
    expect(buffer.usedBytes).toBe(500);

    buffer.setWindow({ playhead: 8, ahead: 1, behind: 3 });
    expect(held(buffer, 20)).toEqual([5, 6, 7, 8]);
    expect(buffer.usedBytes).toBe(400);

    // A frame that left the window and comes back must be sent again.
    buffer.setWindow({ playhead: 0, ahead: 3, behind: 0 });
    expect(held(buffer, 20)).toEqual([]);
    expect(buffer.usedBytes).toBe(0);
  });

  it("clamps the window to the video and refuses a frame beyond the budget", () => {
    const buffer = new FrameBuffer<{ byteLength: number }>(250, 3);
    buffer.setWindow({ playhead: 1, ahead: 10, behind: 10 });
    expect(buffer.add(3, frame(100))).toBe(false);
    expect(buffer.add(0, frame(100))).toBe(true);
    expect(buffer.add(1, frame(100))).toBe(true);
    expect(buffer.add(2, frame(100))).toBe(false);
    expect(buffer.add(1, frame(100))).toBe(true);
    expect(buffer.usedBytes).toBe(200);
  });
});
