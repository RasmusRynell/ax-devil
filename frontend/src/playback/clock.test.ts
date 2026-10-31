// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { PlaybackClock, clampSpeed } from "./clock";
import { FrameBuffer } from "./FrameBuffer";

type Frame = { byteLength: number };

/** Ten frames at 40 ms, a fake clock in milliseconds, and a buffer that holds `buffered` frames. */
function setup(
  times = Float64Array.from({ length: 10 }, (_, id) => id * 40_000),
  buffered?: number[],
) {
  const last = times.length - 1;
  const buffer = new FrameBuffer<Frame>(1_000_000, times.length);
  buffer.setWindow({ playhead: 0, ahead: last, behind: 0 });
  const time = { now: 1000 };
  const clock = new PlaybackClock(times, () => time.now);
  const load = (ids: number[]) => ids.forEach((id) => buffer.add(id, { byteLength: 1 }));
  load(buffered ?? Array.from(times, (_, id) => id));
  /** Advances the fake time, then ticks. */
  const tick = (advanceMs = 0) => {
    time.now += advanceMs;
    return clock.tick(buffer);
  };
  return { clock, buffer, time, tick, load };
}

describe("clock (B26)", () => {
  it("presents every frame of an on-time play and finishes on the last", () => {
    const { clock, tick } = setup();
    clock.play();
    const shown = [tick(), ...Array.from({ length: 12 }, () => tick(40))].filter(
      (id) => id !== undefined,
    );
    expect(shown).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(clock.playing).toBe(false);
  });

  it("skips to the frame due now when late, and renders only that one (B31)", () => {
    const { clock, tick } = setup();
    clock.play();
    expect(tick()).toBe(0);
    expect(tick(205)).toBe(5);
    expect(tick(0)).toBeUndefined();
  });

  it("shows the newest buffered frame before the due one and never slows down or goes backward", () => {
    const { clock, tick, load } = setup(undefined, [0, 1, 2]);
    clock.play();
    expect(tick()).toBe(0);
    expect(tick(200)).toBe(2);
    load([4]);
    expect(tick(0)).toBe(4);
    load([3]);
    expect(tick(0)).toBeUndefined();
    // The clock did not wait for frames 3 and 4: frame 8 is due 120 ms after frame 5 was.
    load([7, 8]);
    expect(tick(120)).toBe(8);
  });

  it("always shows the last frame, even when it arrives after the clock passed it", () => {
    const { clock, tick, load } = setup(undefined, [0, 1, 2]);
    clock.play();
    tick();
    expect(tick(10_000)).toBe(2);
    expect(clock.playing).toBe(true);
    load([9]);
    expect(tick(10)).toBe(9);
    expect(clock.playing).toBe(false);
  });

  it("does not count paused time as lateness", () => {
    const { clock, tick } = setup();
    clock.play();
    tick();
    expect(tick(120)).toBe(3);
    clock.pause();
    clock.play();
    expect(tick(0)).toBeUndefined();
    clock.pause();
    tick(60_000);
    clock.play();
    expect(tick(0)).toBeUndefined();
    expect(tick(40)).toBe(4);
  });

  it.each([
    { elapsedMs: 5, shown: 0 },
    { elapsedMs: 10, shown: 1 },
    { elapsedMs: 99, shown: 2 },
    { elapsedMs: 100, shown: 3 },
    { elapsedMs: 109, shown: 3 },
    { elapsedMs: 110, shown: 4 },
  ])(
    "follows container times of variable-rate video: due frame after $elapsedMs ms",
    ({ elapsedMs, shown }) => {
      const { clock, tick } = setup(Float64Array.from([0, 10_000, 20_000, 100_000, 110_000]));
      clock.play();
      tick();
      tick(elapsedMs);
      expect(clock.info()?.sequenceId).toBe(shown);
    },
  );

  it.each([
    { speed: 0, clamped: 0.01 },
    { speed: -3, clamped: 0.01 },
    { speed: 1.234, clamped: 1.23 },
    { speed: 99, clamped: 10 },
  ])("clamps speed $speed to $clamped", ({ speed, clamped }) => {
    expect(clampSpeed(speed)).toBe(clamped);
  });

  it.each([
    { from: 1, direction: 1, to: 1.1 },
    { from: 1, direction: -1, to: 0.9 },
    { from: 0.01, direction: -1, to: 0.01 },
    { from: 0.01, direction: 1, to: 0.11 },
    { from: 9.95, direction: 1, to: 10 },
  ] as const)("steps speed $from by $direction to $to", ({ from, direction, to }) => {
    const { clock } = setup();
    clock.setSpeed(from);
    clock.stepSpeed(direction);
    expect(clock.speed).toBe(to);
  });

  it("keeps its position when the speed changes while playing", () => {
    const { clock, tick } = setup();
    clock.play();
    tick();
    tick(100);
    clock.setSpeed(2);
    expect(tick(100)).toBe(7);
  });
});

describe("clock position (B27, B28)", () => {
  it("restarts at 0 when played from the last frame", () => {
    const { clock, tick } = setup();
    clock.seek(9);
    tick();
    clock.play();
    expect(clock.playhead).toBe(0);
    expect(clock.playing).toBe(true);
    expect(tick()).toBe(0);
  });

  it.each([
    { name: "seek past the end", act: (c: PlaybackClock) => c.seek(50), at: 9 },
    { name: "seek before the start", act: (c: PlaybackClock) => c.seek(-4), at: 0 },
    { name: "step past the end", act: (c: PlaybackClock) => (c.seek(8), c.step(5)), at: 9 },
    { name: "step before the start", act: (c: PlaybackClock) => c.step(-1), at: 0 },
  ])("clamps: $name", ({ act, at }) => {
    const { clock, tick } = setup();
    act(clock);
    expect(clock.playhead).toBe(at);
    expect(tick()).toBe(at);
  });

  it("steps relative to the requested position while frames are still arriving", () => {
    const { clock, tick, load } = setup(undefined, [0]);
    tick();
    clock.step(1);
    clock.step(1);
    clock.step(1);
    expect(clock.playhead).toBe(3);
    expect(tick()).toBeUndefined();
    expect(clock.info()?.sequenceId).toBe(0);
    load([3]);
    expect(tick()).toBe(3);
    expect(clock.info()).toEqual({ sequenceId: 3, timestampUs: 120_000 });
    clock.step(-2);
    expect(clock.playhead).toBe(1);
  });

  it("steps from the requested position while playing and delivery lags behind it", () => {
    const { clock, tick, load } = setup(undefined, [0, 1, 2]);
    clock.play();
    tick();
    tick(200);
    // Frame 5 is due but only frame 2 is on screen.
    expect(clock.playhead).toBe(5);
    expect(clock.info()?.sequenceId).toBe(2);
    clock.step(1);
    expect(clock.playhead).toBe(6);
    load([6]);
    expect(tick()).toBe(6);
  });

  it("keeps playing after a restart from the end while the first frames are not buffered yet", () => {
    const { clock, tick, load } = setup(undefined, [8, 9]);
    clock.seek(8);
    tick();
    clock.seek(9);
    tick();
    clock.play();
    expect(tick()).toBeUndefined();
    expect(clock.playing).toBe(true);
    load([0]);
    expect(tick()).toBe(0);
  });

  it("opens paused on the start frame", () => {
    const times = Float64Array.from({ length: 10 }, (_, id) => id * 40_000);
    const { buffer } = setup(times);
    const clock = new PlaybackClock(times, () => 0, 6);
    expect(clock.playing).toBe(false);
    expect(clock.tick(buffer)).toBe(6);
  });
});

describe("clock and frames that failed to decode", () => {
  it("never waits for a failed frame while playing", () => {
    const { clock, tick, load } = setup(undefined, [0, 1, 2, 3]);
    clock.skip(4);
    clock.play();
    const shown = [tick(), tick(40), tick(40), tick(40), tick(40)];
    expect(shown).toEqual([0, 1, 2, 3, undefined]);
    load([5]);
    expect(tick(40)).toBe(5);
  });

  it("ends on the last frame that did not fail", () => {
    const { clock, tick } = setup(undefined, [0, 1, 2, 3, 4, 5, 6, 7, 8]);
    clock.skip(9);
    clock.play();
    tick();
    expect(tick(1000)).toBe(8);
    expect(clock.playing).toBe(false);
  });

  it("stops waiting for the last frame once it is reported failed", () => {
    const { clock, tick } = setup(undefined, [0, 1, 2, 3, 4, 5, 6, 7, 8]);
    clock.play();
    tick();
    tick(1000);
    expect(clock.playing).toBe(true);
    clock.skip(9);
    tick(40);
    expect(clock.playing).toBe(false);
  });

  it("stays on the frame on screen when paused on a failed one, and steps on from there", () => {
    const { clock, tick } = setup(undefined, [0, 1, 3]);
    clock.skip(2);
    tick();
    clock.step(1);
    tick();
    clock.step(1);
    expect(tick()).toBeUndefined();
    expect(clock.info()?.sequenceId).toBe(1);
    expect(clock.playhead).toBe(2);
    clock.step(1);
    expect(tick()).toBe(3);
  });
});
