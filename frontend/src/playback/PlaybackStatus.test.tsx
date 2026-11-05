import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { Opening } from "./PlaybackStatus";
import { INITIAL_VIEW, type PlaybackView } from "./view";

afterEach(cleanup);

const opening = (view: Partial<PlaybackView>) => (
  <Opening view={{ ...INITIAL_VIEW, ...view }} name="a.mp4" />
);

describe("the offline pane before the video is ready", () => {
  it("says what it is waiting for in one status, and why it failed instead of spinning", () => {
    const pane = render(opening({ status: "connecting" }));
    expect(screen.getByRole("status").textContent).toBe("Loading");
    pane.rerender(opening({ status: "indexing" }));
    expect(screen.getByRole("status").textContent).toBe("Building frame index: a.mp4");
    pane.rerender(opening({ status: "failed", failure: { reason: "Not a video" } }));
    expect(screen.getByRole("alert").textContent).toBe("Not a video");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
