import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { BookmarkGroupInfo } from "../generated/api";
import { entry, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { SessionProvider, useFollowing, useSession, useView } from "../session/session";
import { KeptStateProvider } from "../kit/keptState";
import { BookmarksView } from "./BookmarksView";
import {
  SeekProvider,
  useAddBookmark,
  useAskSeek,
  useSidebarOnReplace,
  useSeekRequest,
} from "./viewerLink";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const CLIP: BookmarkGroupInfo = {
  key: "v",
  item: "v",
  item_name: "v.mp4",
  entry: "",
  target: null,
  reviewed: false,
  standing: { status: "Open", entry: "content-v" },
  bookmarks: [
    { id: "b1", frame: 75, time_us: 3_000_000, kind: "note", note: "" },
    { id: "b2", frame: 90, time_us: 3_600_000, kind: "issue", note: "Blur" },
  ],
};
const GONE: BookmarkGroupInfo = {
  key: "mot/MOT/Z",
  item: "mot",
  item_name: "mot",
  entry: "MOT/Z",
  target: "MOT/Z",
  reviewed: true,
  standing: { status: "Missing" },
  bookmarks: [{ id: "b3", frame: 4, time_us: 160_000, kind: "note", note: "" }],
};

function Front() {
  const [viewers] = useView("viewers");
  const [active] = useView("active");
  return <div data-testid="front">{JSON.stringify({ active, viewers })}</div>;
}

/** The view as the sidebar shows it: gone and back again when another view is chosen. */
function Switch() {
  const [shown, setShown] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setShown((now) => !now)}>
        Switch
      </button>
      {shown && <BookmarksView />}
    </>
  );
}

async function renderView(groups: BookmarkGroupInfo[]) {
  render(
    <SessionProvider>
      <KeptStateProvider>
        <SeekProvider>
          <Switch />
          <Front />
        </SeekProvider>
      </KeptStateProvider>
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  room.bookmarks = groups;
  act(() =>
    room.setItems([video("v", false), playlist("mot", [entry("mot/MOT/A", "A", ["GT"])], false)]),
  );
  return room;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );

describe("the Bookmarks view", () => {
  it("says there is nothing, and offers no search or export, until a bookmark exists", async () => {
    await renderView([]);
    expect(screen.getByText("No bookmarks")).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
  });

  it("ignores the search and kind it had once the last bookmark is gone and the controls are hidden", async () => {
    const room = await renderView([CLIP, { ...GONE, bookmarks: [] }]);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search bookmarks" }), {
      target: { value: "nothing like it" },
    });
    expect(screen.getByText("No matches")).toBeTruthy();
    room.bookmarks = [{ ...GONE, bookmarks: [] }];
    act(() => room.setItems([video("v", false)]));
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.getByRole("region", { name: "mot › MOT/Z" })).toBeTruthy();
  });

  it("lists the groups by frame, and a missing target says so and cannot open", async () => {
    await renderView([CLIP, GONE]);
    expect(screen.getByText("0:03.00 · frame 75 · Note")).toBeTruthy();
    expect(screen.getByText("Blur")).toBeTruthy();
    expect(screen.getByRole("region", { name: "mot › MOT/Z" })).toBeTruthy();
    expect(screen.getByText("Missing")).toBeTruthy();
    fireEvent.click(screen.getByText("Frame 4"));
    expect(sent()).toEqual([]);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search bookmarks" }), {
      target: { value: "nothing like it" },
    });
    expect(screen.getByText("No matches")).toBeTruthy();
  });

  it("opens a bookmark's video in front, and a playlist entry's entry", async () => {
    await renderView([
      CLIP,
      {
        ...GONE,
        standing: { status: "Open", entry: "mot/MOT/A" },
        bookmarks: GONE.bookmarks,
      },
    ]);
    fireEvent.click(screen.getByText("Frame 75"));
    await act(async () => {});
    expect(sent()).toEqual([{ type: "openViewer", item: "v" }]);
    expect(JSON.parse(screen.getByTestId("front").textContent ?? "").active).toBe(1);

    fireEvent.click(screen.getByText("Frame 4"));
    await act(async () => {});
    const { viewers } = JSON.parse(screen.getByTestId("front").textContent ?? "");
    expect(viewers[2]).toMatchObject({ item: "mot", entry: "mot/MOT/A" });
  });

  it("marks the bookmark opened last, through a switch of view and a reorder, until a bookmark changes or the workspace is replaced", async () => {
    const mot = { ...GONE, standing: { status: "Open", entry: "mot/MOT/A" } } as const;
    const room = await renderView([CLIP, mot]);
    const items = () =>
      act(() =>
        room.setItems([
          video("v", false),
          playlist("mot", [entry("mot/MOT/A", "A", ["GT"])], false),
        ]),
      );
    const marked = () =>
      screen
        .queryAllByRole("button", { current: true })
        .map((row) => row.querySelector("span span")?.textContent);
    expect(marked()).toEqual([]);
    fireEvent.click(screen.getByText("Blur"));
    await act(async () => {});
    expect(marked()).toEqual(["Blur"]);

    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(marked()).toEqual(["Blur"]);

    // The groups in another order, one item renamed: the mark stays.
    room.bookmarks = [mot, { ...CLIP, item_name: "w.mp4" }];
    items();
    expect(marked()).toEqual(["Blur"]);

    // The same bookmarks in a workspace that replaced the open one: the mark goes.
    room.epoch += 1;
    items();
    expect(marked()).toEqual([]);

    fireEvent.click(screen.getByText("Blur"));
    await act(async () => {});
    expect(marked()).toEqual(["Blur"]);
    const [first, second] = CLIP.bookmarks;
    room.bookmarks = [mot, { ...CLIP, bookmarks: [first, { ...second, note: "Sharp" }] }];
    items();
    expect(marked()).toEqual([]);
  });

  it("renames, changes the kind of, removes and reviews through the backend", async () => {
    await renderView([CLIP]);
    const bookmark = (command: object) => ({ type: "bookmark", command });

    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Kind" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Question" }));
    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("Blur");
    fireEvent.change(name, { target: { value: " Frame 90 " } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    fireEvent.click(screen.getByRole("button", { name: "To review" }));
    await act(async () => {});

    expect(sent()).toEqual([
      bookmark({ type: "remove", id: "b2" }),
      bookmark({ type: "edit", id: "b2", kind: "question" }),
      // The label it has without text is no text.
      bookmark({ type: "edit", id: "b2", note: "" }),
      bookmark({ type: "setReviewed", item: "v", reviewed: true }),
    ]);
  });

  it("exports every bookmark to a typed path where the window has no file dialog", async () => {
    await renderView([CLIP]);
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(screen.getByText("Enter a path on the server.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Path on the server"), {
      target: { value: "/tmp/marks" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    const workspace = FakeSocket.latest.received.flatMap((message) =>
      message.type === "command" && message.cmd.type === "workspace" ? [message.cmd.command] : [],
    );
    expect(workspace).toEqual([{ type: "exportBookmarks", path: "/tmp/marks.csv" }]);
  });
});

/** Stands for the playback session of an entry that is on its frame. */
type Status = "ready" | "indexing";
function stub(status: Status) {
  const calls: string[] = [];
  const session = {
    getSnapshot: () => ({
      status,
      frameCount: 100,
      playhead: 9,
      shown: { sequenceId: 7, timestampUs: 280_000.4, periodUs: 40_000 },
    }),
    frameTime: () => undefined,
    seek: (frame: number) => calls.push(`seek ${frame}`),
    pause: () => calls.push("pause"),
  };
  return { session: session as never, calls };
}

describe("a viewer and its bookmarks", () => {
  it("shows the Bookmarks view for a workspace that replaces the open one and has bookmarks, Workspace for a recovered one, not for the first", async () => {
    function Sidebar() {
      useSidebarOnReplace();
      const [sidebar] = useView("sidebar");
      return <div data-testid="view">{sidebar.view}</div>;
    }
    render(
      <SessionProvider>
        <Sidebar />
      </SessionProvider>,
    );
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1") as Room;
    room.bookmarks = [CLIP];
    act(() => room.setItems([video("v", false)]));
    expect(screen.getByTestId("view").textContent).toBe("workspace");
    room.epoch = 1;
    act(() => room.setItems([video("v", false)]));
    expect(screen.getByTestId("view").textContent).toBe("bookmarks");
    room.epoch = 2;
    room.recovered = true;
    act(() => room.setItems([video("v", false)]));
    expect(screen.getByTestId("view").textContent).toBe("workspace");
  });

  it("treats a workspace of another session at the same epoch as a replacement, and the same session again after a reload as none", async () => {
    // This window last saw epoch 1 of a session that is gone; the new session starts with a bookmarked workspace at 1.
    window.sessionStorage.setItem(
      "ax-devil.sidebarEpoch",
      JSON.stringify({ session: "gone", epoch: 1 }),
    );
    FakeSocket.launch = { epoch: 1, bookmarks: [CLIP] };
    function Sidebar() {
      const [shown, setShown] = useState(true);
      const [sidebar, setSidebar] = useView("sidebar");
      return (
        <>
          <button type="button" onClick={() => setShown((now) => !now)}>
            reload
          </button>
          <button type="button" onClick={() => setSidebar({ view: "workspace", open: true })}>
            choose
          </button>
          {shown && <Hook />}
          <div data-testid="view">{sidebar.view}</div>
        </>
      );
    }
    function Hook() {
      useSidebarOnReplace();
      return null;
    }
    render(
      <SessionProvider>
        <Sidebar />
      </SessionProvider>,
    );
    await act(async () => {});
    expect(screen.getByTestId("view").textContent).toBe("bookmarks");

    fireEvent.click(screen.getByRole("button", { name: "choose" }));
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    await act(async () => {});
    expect(screen.getByTestId("view").textContent).toBe("workspace");
  });

  it("leaves a watcher following the presenter when a workspace with bookmarks replaces the one it joined", async () => {
    const host = FakeSocket.host("explore");
    host.epoch = 1;
    host.bookmarks = [CLIP];
    function Watcher() {
      useSidebarOnReplace();
      const { watch } = useSession();
      const [sidebar] = useView("sidebar");
      const following = useFollowing();
      return (
        <button type="button" onClick={() => watch(host.id)}>
          {`${sidebar.view} ${following ? "following" : "exploring"}`}
        </button>
      );
    }
    render(
      <SessionProvider>
        <Watcher />
      </SessionProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button"));
    await act(async () => {});
    expect(screen.getByRole("button").textContent).toBe("workspace following");

    act(() => host.publish({ ...host.presenter, sidebar: { view: "plugins", open: true } }));
    expect(screen.getByRole("button").textContent).toBe("plugins following");
    const seen = FakeSocket.latest.received.filter((message) => message.type === "view");
    expect(seen).toEqual([]);
  });

  it("keeps a watcher that chose Follow presenter following when the replacement's Doc arrives before its seat", async () => {
    const host = FakeSocket.host("explore");
    function Watcher() {
      useSidebarOnReplace();
      const { watch, follow } = useSession();
      const [sidebar, setSidebar] = useView("sidebar");
      const following = useFollowing();
      return (
        <>
          <button type="button" onClick={() => watch(host.id)}>
            watch
          </button>
          <button type="button" onClick={() => setSidebar({ view: "plugins", open: true })}>
            explore
          </button>
          <button type="button" onClick={() => void follow()}>
            follow
          </button>
          <div data-testid="state">{`${sidebar.view} ${following ? "following" : "exploring"}`}</div>
        </>
      );
    }
    render(
      <SessionProvider>
        <Watcher />
      </SessionProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "watch" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "explore" }));
    await waitFor(() => expect(host.seats[1].view).not.toBeNull());
    expect(screen.getByTestId("state").textContent).toBe("plugins exploring");

    // The backend coalesces: the replacement's Doc goes out, and only the latest seat follows it.
    const seats: Parameters<FakeSocket["emit"]>[0][] = [];
    const emit = FakeSocket.prototype.emit;
    const hold = vi.spyOn(FakeSocket.prototype, "emit").mockImplementation(function (
      this: FakeSocket,
      message,
    ) {
      if (message.type === "seat") seats.push(message);
      else emit.call(this, message);
    });
    fireEvent.click(screen.getByRole("button", { name: "follow" }));
    await act(async () => {});
    host.epoch = 1;
    host.bookmarks = [CLIP];
    act(() => host.setItems([video("v", false)]));
    await act(async () => {});
    hold.mockRestore();
    act(() => FakeSocket.latest.emit(seats[seats.length - 1]));
    await act(async () => {});
    expect(screen.getByTestId("state").textContent).toBe("workspace following");
  });

  it("bookmarks the frame on screen and brings the Bookmarks view up", async () => {
    const { session } = stub("ready");
    function Add() {
      const add = useAddBookmark(session, "v", "content-v");
      const [sidebar] = useView("sidebar");
      return (
        <button type="button" onClick={add}>
          {sidebar.view}
        </button>
      );
    }
    render(
      <SessionProvider>
        <Add />
      </SessionProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "workspace" }));
    await act(async () => {});
    expect(sent()).toEqual([
      {
        type: "bookmark",
        command: { type: "add", item: "v", entry: "content-v", frame: 7, timeUs: 280_000 },
      },
    ]);
    expect(screen.getByRole("button", { name: "bookmarks" })).toBeTruthy();
  });

  it("shows the frame it was asked for, paused and within the video, once it is ready, and only for its own entry", async () => {
    const mine = stub("ready");
    const other = stub("ready");
    function Viewer({ entry, ready, status }: { entry: string; ready: boolean; status: Status }) {
      useSeekRequest(
        (entry === "other" ? other : mine).session,
        ready ? status : "indexing",
        entry,
      );
      return null;
    }
    function Page() {
      const [ready, setReady] = useState(false);
      const ask = useAskSeek();
      return (
        <>
          <Viewer entry="other" ready status="ready" />
          <Viewer entry="content-v" ready={ready} status="ready" />
          <button type="button" onClick={() => ask({ entry: "content-v", frame: 500 })}>
            ask
          </button>
          <button type="button" onClick={() => setReady(true)}>
            ready
          </button>
        </>
      );
    }
    render(
      <SessionProvider>
        <SeekProvider>
          <Page />
        </SeekProvider>
      </SessionProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    expect(mine.calls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "ready" }));
    expect(mine.calls).toEqual(["pause", "seek 99"]);
    expect(other.calls).toEqual([]);
  });
});
