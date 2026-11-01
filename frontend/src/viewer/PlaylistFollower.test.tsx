import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { opened } from "../playback/fakeOpened";
import type { Incoming } from "../playback/wire";
import { entry, FakeSocket, playlist } from "../session/fakeSession";
import { SessionProvider, useDoc } from "../session/session";
import { PlaylistViewer } from "./PlaylistViewer";

// The picture needs WebGL, and the socket is the test's: it opens a 100-frame video as soon as it is asked.
vi.mock("../playback/VideoCanvas", () => ({ VideoCanvas: () => null }));
const sockets = vi.hoisted(() => [] as ((incoming: Incoming) => void)[]);
vi.mock("../playback/connect", () => ({
  connectWorker: (onIncoming: (incoming: Incoming) => void) => {
    sockets.push(onIncoming);
    return {
      send: (message: { type: string }) => {
        if (message.type !== "Open") return;
        act(() => {
          onIncoming({ type: "message", message: opened(100, 24) });
          const times = Float64Array.from({ length: 100 }, (_, id) => id * 40_000);
          onIncoming({ type: "envelope", envelope: { kind: "timeline", times } });
        });
      },
      close: () => {},
    };
  },
}));

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  sockets.length = 0;
});

function Host() {
  const resolution = useDoc()?.workspace.items.find((item) => item.id === "list")?.resolution;
  return (
    resolution?.status === "Resolved" &&
    resolution.content.kind === "Playlist" && (
      <PlaylistViewer item="list" content={resolution.content} />
    )
  );
}

/** A window that follows a presenter who shows entry A of a playlist, with its stream open. */
async function follow() {
  const room = FakeSocket.host("watch");
  room.setItems([playlist("list", [entry("list/A"), entry("list/B")])]);
  const show = (entryId: string) =>
    room.publish({
      sidebar: room.presenter.sidebar,
      active: "list",
      viewers: { list: { entry: entryId } },
    });
  show("list/A");
  window.sessionStorage.setItem(
    "ax-devil.credential",
    JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
  );
  render(
    <SessionProvider>
      <Host />
    </SessionProvider>,
  );
  await screen.findByRole("slider", { name: "Timeline" });
  // The screen has subscribed to its session by now; a stream that ends before that is not shown until the next render.
  await act(async () => {});
  return { show };
}

const tickets = () =>
  FakeSocket.sockets
    .flatMap((socket) => socket.received)
    .flatMap((message) =>
      message.type === "command" && message.cmd.type === "requestTicket" ? [message.cmd.key] : [],
    );

describe("a following window on a playlist", () => {
  it("moves to the presenter's next entry even when the backend ends the old stream first", async () => {
    const { show } = await follow();
    // The backend revokes the follower's grant for entry A before the new view reaches this window.
    await act(async () => sockets.at(-1)?.({ type: "closed", code: 4403 }));
    expect((await screen.findByRole("alert")).textContent).toContain("access to it was withdrawn");

    act(() => show("list/B"));
    await act(async () => {});
    expect(screen.queryByRole("alert")).toBeNull();
    expect(await screen.findByRole("slider", { name: "Timeline" })).toBeTruthy();
    expect(tickets().at(-1)).toMatchObject({ content: "list", entry: "list/B" });
  });

  it("moves to it without any notice when the new view comes first", async () => {
    const { show } = await follow();
    act(() => show("list/B"));
    await act(async () => {});
    await act(async () => sockets[0]({ type: "closed", code: 4403 }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(await screen.findByRole("slider", { name: "Timeline" })).toBeTruthy();
    expect(tickets().at(-1)).toMatchObject({ entry: "list/B" });
  });
});
