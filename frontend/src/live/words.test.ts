import { expect, it } from "vite-plus/test";
import { headline, problemLine, utcTime } from "./words";

it("words a feed as Python's viewer does and a capture time as the UTC time of day", () => {
  const reconnecting = { status: "Reconnecting", attempt: 2, reason: "Unreachable" } as const;
  expect(headline(reconnecting)).toBe("Reconnecting (2)");
  expect(problemLine("Video", reconnecting)).toBe("Video: Reconnecting (2) — Unreachable");
  expect(problemLine("Overlay", { status: "Failed", reason: "LoginRejected" })).toBe(
    "Overlay: ✕ Failed — Login rejected",
  );
  expect(problemLine("Video", { status: "Stalled", seconds: 5 })).toBe(
    "Video: Stalled — No frames for 5 s",
  );
  expect(problemLine("Video", { status: "Connecting" })).toBeNull();
  expect(utcTime(1_700_000_012_345_678)).toBe("22:13:32.345678");
  expect(utcTime(5)).toBe("00:00:00.000005");
});
