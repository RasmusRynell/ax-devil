import { expect, it } from "vite-plus/test";
import { feedText, utcTime } from "./words";

it("words a feed in plain language and a capture time as the UTC time of day", () => {
  expect(feedText({ status: "Reconnecting", attempt: 2, reason: "Unreachable" })).toBe(
    "Reconnecting (2) — Unreachable",
  );
  expect(feedText({ status: "Failed", reason: "LoginRejected" })).toBe("Failed — Login rejected");
  expect(feedText({ status: "Stalled", seconds: 5 })).toBe("Stalled — No frames for 5 s");
  expect(feedText({ status: "Connecting" })).toBe("Connecting…");
  expect(utcTime(1_700_000_012_345_678)).toBe("22:13:32.345678");
  expect(utcTime(5)).toBe("00:00:00.000005");
});
