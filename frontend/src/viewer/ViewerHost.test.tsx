import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSocket, resolved, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import type { ViewerProps } from "../viewers";
import { ViewerHost } from "./ViewerHost";
import { useFrontViewer } from "./useViewers";

vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: ({ content }: ViewerProps<"SeekableVideo">) => <p>playing {content.id}</p>,
}));

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** The host as the shell runs it: the owner's window brings the newest viewer to the front. */
function Host() {
  useFrontViewer();
  return <ViewerHost />;
}

async function setup() {
  render(
    <SessionProvider>
      <Host />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1")!;
  return (...items: Parameters<typeof video>[]) =>
    act(() => room.setItems(items.map((item) => video(...item))));
}

describe("viewer host", () => {
  it("shows an item as opening until it resolves, then its viewer", async () => {
    const send = await setup();
    send(["a", true, { status: "Pending" }]);
    expect(screen.getByRole("status").textContent).toBe("Opening a.mp4…");
    send(["a", true, resolved("a")]);
    expect(screen.getByText("playing content-a")).toBeTruthy();
  });

  it("says why an item failed, and where only when the backend told it", async () => {
    const send = await setup();
    const failed = { status: "Failed" as const, code: "FileNotFound" as const, params: {} };
    send(["a", true, { ...failed, reason: "no such file", location: "/clips/a.mp4" }]);
    expect(screen.getByRole("alert").textContent).toBe("no such file/clips/a.mp4");
    send(["a", true, failed]);
    expect(screen.getByRole("alert").textContent).toBe("The file was not found.");
  });

  it("offers to reload a viewer whose files changed, once per change, and reloading resolves the item", async () => {
    const send = await setup();
    const offer = () => screen.queryByText("Source changed, reload current viewer?");
    send(["a", true, resolved("a")]);
    expect(offer()).toBeNull();

    const room = FakeSocket.rooms.get("room-1")!;
    const set = (count: number) => act(() => room.setItems([{ ...video("a"), changed: count }]));
    set(1);
    expect(offer()).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(offer()).toBeNull();
    set(1);
    expect(offer()).toBeNull();
    set(2);
    expect(offer()).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "resolve", item: "a" } },
      }),
    );
  });

  it("switches between open items, and closing one shows the other", async () => {
    const send = await setup();
    send(["a", true, resolved("a")], ["b", true, resolved("b")]);
    expect(screen.getByText("playing content-b")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "a.mp4" }));
    expect(screen.getByText("playing content-a")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close a.mp4" }));
    expect(screen.getByText("playing content-b")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "a.mp4" })).toBeNull();
  });
});
