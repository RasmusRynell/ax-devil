import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { Opening } from "./PlaybackStatus";
import { INITIAL_VIEW, type PlaybackView } from "./view";

afterEach(cleanup);

const showing = (view: Partial<PlaybackView>) =>
  render(<Opening view={{ ...INITIAL_VIEW, ...view }} name="a.mp4" />);

describe("the offline pane before the video is ready", () => {
  it("covers it with a busy indicator that names the work, and says why it failed instead of spinning", () => {
    const opening = showing({ status: "connecting" });
    expect(screen.getByRole("status").textContent).toBe("");
    opening.unmount();
    const indexing = showing({ status: "indexing", progress: 0.4 });
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Building frame index: a.mp4");
    indexing.unmount();
    showing({ status: "failed", failure: { reason: "Not a video" } });
    expect(screen.getByRole("alert").textContent).toBe("Not a video");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
