import { useEffect, useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ViewerProps } from "../viewers";
import { fakeWorkspace, pending, resolved, video } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { ViewerHost } from "./ViewerHost";

// A viewer with a position of its own, kept in the memory the host gives it.
vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: ({ content, memory }: ViewerProps<"SeekableVideo">) => {
    const [frame, setFrame] = useState((memory.saved as number | undefined) ?? 0);
    const latest = useRef(frame);
    latest.current = frame;
    useEffect(() => () => memory.save(latest.current), []);
    return (
      <button type="button" onClick={() => setFrame(frame + 1)}>
        playing {content.id} at {frame}
      </button>
    );
  },
}));

afterEach(cleanup);

function setup() {
  const workspace = fakeWorkspace();
  render(
    <WorkspaceProvider store={workspace.store}>
      <ViewerHost />
    </WorkspaceProvider>,
  );
  const send = (...items: Parameters<typeof video>[]) =>
    act(() => workspace.source().send(items.map((item) => video(...item))));
  return { send, workspace };
}

describe("viewer host", () => {
  it("shows an item as opening until it resolves, then its viewer", () => {
    const { send } = setup();
    send(["a", true, pending]);
    expect(screen.getByRole("status").textContent).toBe("Opening a.mp4…");
    send(["a", true, resolved("a")]);
    expect(screen.getByText("playing content-a at 0")).toBeTruthy();
  });

  it("says why an item failed, and where", () => {
    const { send } = setup();
    send(["a", true, { status: "Failed", reason: "no such file", location: "/clips/a.mp4" }]);
    expect(screen.getByRole("alert").textContent).toBe("no such file/clips/a.mp4");
  });

  it("switches between open items, and closing one shows the other", () => {
    const { send } = setup();
    send(["a", true, resolved("a")], ["b", true, resolved("b")]);
    expect(screen.getByText("playing content-b at 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "a.mp4" }));
    expect(screen.getByText("playing content-a at 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close a.mp4" }));
    expect(screen.getByText("playing content-b at 0")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "a.mp4" })).toBeNull();
  });

  it("returns to an item where it was left, and forgets that when its tab is closed", () => {
    const { send, workspace } = setup();
    send(["a", true, resolved("a")], ["b", true, resolved("b")]);
    fireEvent.click(screen.getByRole("tab", { name: "a.mp4" }));
    fireEvent.click(screen.getByText("playing content-a at 0"));
    fireEvent.click(screen.getByText("playing content-a at 1"));
    fireEvent.click(screen.getByRole("tab", { name: "b.mp4" }));
    send(["a", true, resolved("a")], ["b", true, resolved("b")]);
    fireEvent.click(screen.getByRole("tab", { name: "a.mp4" }));
    expect(screen.getByText("playing content-a at 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close a.mp4" }));
    expect(workspace.store.remembered("a")).toBeUndefined();
  });
});
