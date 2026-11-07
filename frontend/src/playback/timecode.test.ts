import { expect, it } from "vite-plus/test";
import { timecode } from "./timecode";

it("rounds half to even, as Python does", () => {
  expect([4_999, 5_000, 15_000, 25_000, 5_001].map((us) => timecode(us))).toEqual([
    "0:00.00",
    "0:00.00",
    "0:00.02",
    "0:00.02",
    "0:00.01",
  ]);
  expect([2_500_000, 3_500_000, 3_661_000_000].map((us) => timecode(us, false))).toEqual([
    "0:02",
    "0:04",
    "1:01:01",
  ]);
});
