import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    pane.rerender(
      opening({
        status: "failed",
        failure: { code: "NotAVideo", params: {}, reason: "Not a video" },
      }),
    );
    expect(screen.getByRole("alert").textContent).toBe("Not a video");
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
    pane.rerender(opening({ status: "connecting" }));
    pane.rerender(
      opening({
        status: "failed",
        failure: { code: "NotAVideo", params: {}, reason: "Not a video" },
      }),
    );
    expect(screen.getByRole("alert").textContent).toBe("Not a video");
    pane.rerender(opening({ status: "failed", failure: { code: "NotAVideo", params: {} } }));
    expect(screen.getByRole("alert").textContent).toBe("The file is not a video.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
