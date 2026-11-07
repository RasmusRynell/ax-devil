import { expect, it } from "vite-plus/test";
import { timecode } from "./timecode";

// The expectations are what Python's `round((us / 1e6) * 100)` and `round(us / 1e6) * 100` give for the same doubles.
it("reads as the reference does: half to even on the same floating-point arithmetic", () => {
  expect([4_999, 5_000, 5_001, 15_000, 25_000, 545_000, 575_000].map((us) => timecode(us))).toEqual(
    ["0:00.00", "0:00.00", "0:00.01", "0:00.02", "0:00.02", "0:00.55", "0:00.57"],
  );
  expect([2_500_000, 3_500_000, 3_661_000_000].map((us) => timecode(us, false))).toEqual([
    "0:02",
    "0:04",
    "1:01:01",
  ]);
});
