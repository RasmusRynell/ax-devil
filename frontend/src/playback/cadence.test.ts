// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { cadence } from "./cadence";

describe("cadence", () => {
  it("is constant for evenly spaced frames", () => {
    expect(cadence([0, 40_000, 80_000, 120_000])).toEqual({ variable: false, periodsUs: [40_000] });
  });

  it("is variable when any two gaps differ, even by a microsecond, keeping the five most common", () => {
    expect(cadence([0, 33_000, 67_000, 100_000, 133_000])).toEqual({
      variable: true,
      periodsUs: [33_000, 34_000],
    });
    // Gaps 1 to 7 us, the first three twice as common; equally common gaps keep the order they first occur in.
    const gaps = [3, 3, 1, 1, 2, 2, 4, 5, 6, 7];
    const times = gaps.reduce<number[]>((all, gap) => [...all, all[all.length - 1] + gap], [0]);
    expect(cadence(times)).toEqual({ variable: true, periodsUs: [3, 1, 2, 4, 5] });
  });

  it("ignores frames that share a time", () => {
    expect(cadence([0, 0, 40_000])).toEqual({ variable: false, periodsUs: [40_000] });
  });

  it("is unknown without two frames apart", () => {
    expect(cadence([0])).toBeNull();
    expect(cadence([5, 5])).toBeNull();
  });
});
