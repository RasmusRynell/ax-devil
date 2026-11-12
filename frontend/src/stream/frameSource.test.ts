// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { displaySource, PENDING_MS } from "./frameSource";

function source(ready: boolean) {
  const time = { now: 1000, since: 1000 };
  const frames = displaySource(
    { has: (id) => id === 3, newestPresentable: () => 3 },
    { ready: () => ready },
    () => time.since,
    () => time.now,
  );
  return { frames, time };
}

describe("when a frame may be shown", () => {
  it("never holds the video back for data while playing", () => {
    expect(source(false).frames.newestPresentable(5, 0)).toBe(3);
  });

  it("waits for lane data after a seek, but no longer than a moment", () => {
    const { frames, time } = source(false);
    expect(frames.has(3)).toBe(false);
    time.now += PENDING_MS - 1;
    expect(frames.has(3)).toBe(false);
    time.now += 1;
    expect(frames.has(3)).toBe(true);
  });

  it("shows a frame at once when the lanes have their data, and never one the video lacks", () => {
    const { frames, time } = source(true);
    expect(frames.has(3)).toBe(true);
    time.now += 10 * PENDING_MS;
    expect(frames.has(4)).toBe(false);
  });
});
