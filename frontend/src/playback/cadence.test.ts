// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { cadence } from "./cadence";

describe("cadence", () => {
  it("is constant for evenly spaced frames", () => {
    expect(cadence([0, 40_000, 80_000, 120_000])).toEqual({ variable: false, periodsUs: [40_000] });
  });

  it("is variable when two gaps differ by more than a microsecond, keeping the five most common", () => {
    expect(cadence([0, 33_000, 67_000, 100_000, 133_000])).toEqual({
      variable: true,
      periodsUs: [33_000, 34_000],
    });
    // Gaps 1 to 7 ms, the first three twice as common; equally common gaps keep the order they first occur in.
    const gaps = [3, 3, 1, 1, 2, 2, 4, 5, 6, 7].map((gap) => gap * 1000);
    const times = gaps.reduce<number[]>((all, gap) => [...all, all[all.length - 1] + gap], [0]);
    expect(cadence(times)).toEqual({ variable: true, periodsUs: [3000, 1000, 2000, 4000, 5000] });
  });

  it("is constant when microsecond rounding of the frame times moves a gap by one", () => {
    // 30 fps in a 1/90000 base, then 29.97 fps: each time is rounded on its own.
    expect(cadence([0, 33_333, 66_667, 100_000, 133_333, 166_667, 200_000])).toEqual({
      variable: false,
      periodsUs: [33_333],
    });
    expect(cadence([0, 33_367, 66_733, 100_100, 133_467])).toEqual({
      variable: false,
      periodsUs: [33_367],
    });
  });

  it("is unknown without two frames apart", () => {
    expect(cadence([0])).toBeNull();
  });
});
