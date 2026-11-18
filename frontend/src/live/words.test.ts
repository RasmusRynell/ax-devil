import { expect, it } from "vite-plus/test";
import type { LiveFeedStatus } from "../generated/api";
import { headline, needsRetry, placeholderLines, problemLine, utcTime } from "./words";

it("words a feed as Python's viewer does and a capture time as the UTC time of day", () => {
  const reconnecting = { status: "Reconnecting", attempt: 2, reason: "Unreachable" } as const;
  expect(headline(reconnecting)).toBe("Reconnecting (2)");
  expect(problemLine("Video", reconnecting)).toBe("Video: Reconnecting (2) — Unreachable");
  expect(problemLine("Overlay", { status: "Failed", reason: "LoginRejected" })).toBe(
    "Overlay: ✕ Failed — Login rejected",
  );
  expect(problemLine("Overlay", { status: "Failed", reason: "SourceUnavailable" })).toBe(
    "Overlay: ✕ Failed — Data source not available. Choose another in Edit.",
  );
  for (const alternatives of [
    "none",
    "scene#2, tracks#2",
    "a#2, b#2, c#2, d#2, e#2, f#2, g#2, h#2 (+3 more)",
  ]) {
    expect(
      problemLine("Overlay", { status: "Failed", reason: "SourceUnavailable" }, alternatives),
    ).toBe(
      `Overlay: ✕ Failed — Data source not available. Available sources: ${alternatives}. Choose another in Edit.`,
    );
  }
  expect(problemLine("Video", { status: "Stalled", seconds: 5 })).toBe(
    "Video: Stalled — No frames for 5 s",
  );
  expect(problemLine("Video", { status: "Connecting" })).toBeNull();
  // The empty pane says the same of the video, whether or not it is a problem.
  expect(placeholderLines({ status: "Connecting" })).toEqual([
    "Connecting…",
    "Waiting for the device to answer.",
  ]);
  expect(placeholderLines(reconnecting)).toEqual(["Reconnecting (2)", "Unreachable"]);
  // Retry is offered for a feed only a manual retry recovers, not for one that is connecting or retrying itself.
  const feeds: LiveFeedStatus[] = [
    { status: "Connecting" },
    { status: "Live" },
    reconnecting,
    { status: "Stalled", seconds: 5 },
    { status: "Failed", reason: "Lost" },
  ];
  expect(feeds.map(needsRetry)).toEqual([false, false, false, true, true]);
  expect(utcTime(1_700_000_012_345_678)).toBe("22:13:32.345678");
  expect(utcTime(5)).toBe("00:00:00.000005");
});
