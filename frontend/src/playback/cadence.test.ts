// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { cadence } from "./cadence";

describe("cadence", () => {
  it("is constant for evenly spaced frames, also when a millisecond time base rounds them", () => {
    expect(cadence([0, 40_000, 80_000, 120_000])).toEqual({ variable: false, periodsUs: [40_000] });
    expect(cadence([0, 33_000, 67_000, 100_000, 133_000])).toEqual({
      variable: false,
      periodsUs: [33_000, 34_000],
    });
  });

  it("is variable when a frame is missing, listing the most common gaps first", () => {
    expect(cadence([0, 40_000, 120_000, 160_000, 200_000])).toEqual({
      variable: true,
      periodsUs: [40_000, 80_000],
    });
  });

  it("is unknown for a single frame", () => {
    expect(cadence([0])).toBeNull();
  });
});
