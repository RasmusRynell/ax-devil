import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, renderHook } from "@testing-library/react";
import { useReportStatus, useStreamStatuses } from "./streamState";
import type { LiveView } from "./view";

afterEach(cleanup);

const live = {
  status: "ready",
  video: { status: "Live" },
  overlay: null,
  paused: false,
} as LiveView;
const stalled = {
  ...live,
  video: { status: "Stalled", seconds: 4 },
} as LiveView;

describe("stream status", () => {
  it("keeps what the panes of one item still showing it report when another closes", () => {
    const first = renderHook(() => useReportStatus(1, "cam", live));
    renderHook(() => useReportStatus(2, "cam", stalled));
    const statuses = renderHook(() => useStreamStatuses());
    // The most urgent of the two.
    expect(statuses.result.current.get("cam")?.state).toBe("stalled");
    first.unmount();
    expect(statuses.result.current.get("cam")?.state).toBe("stalled");
    cleanup();
    expect(renderHook(() => useStreamStatuses()).result.current.has("cam")).toBe(false);
  });
});
