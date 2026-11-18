import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { scenario } from "../session/fakeScenario";
import { FakeSocket, type Room } from "../session/fakeSession";
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

const marks = scenario("bookmarks");

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

async function renderView(state?: string) {
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
  if (state) act(() => room.show(marks, state));
  return room;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );

describe("the Bookmarks view", () => {
  it("says there is nothing, and offers no search or export, until a bookmark exists", async () => {
    await renderView();
    expect(screen.getByText("No bookmarks")).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
  });

  it("ignores the search and kind it had once the last bookmark is gone and the controls are hidden", async () => {
    const room = await renderView("initial");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search bookmarks" }), {
      target: { value: "nothing like it" },
    });
    expect(screen.getByText("No matches")).toBeTruthy();
    act(() => room.show(marks, "reviewed"));
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.getByRole("region", { name: "mot › A" })).toBeTruthy();
  });

  it("lists the groups by frame, and a missing target says so and cannot open", async () => {
    await renderView("initial");
    expect(screen.getByText("0:03.00 · frame 75 · Note")).toBeTruthy();
    expect(screen.getByText("0:03.60 · frame 90 · Issue")).toBeTruthy();
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
    await renderView("initial");
    fireEvent.click(screen.getByText("Frame 75"));
    await act(async () => {});
    expect(sent()).toEqual([{ type: "openViewer", item: marks.ids.v }]);
    expect(JSON.parse(screen.getByTestId("front").textContent ?? "").active).toBe(1);

    fireEvent.click(screen.getByText("Frame 6"));
    await act(async () => {});
    const { viewers } = JSON.parse(screen.getByTestId("front").textContent ?? "");
    expect(viewers[2]).toMatchObject({ item: marks.ids.mot, entry: marks.ids["mot/A"] });
  });

  it("marks the bookmark opened last, through a switch of view and an item renamed, until a bookmark changes or the workspace is replaced", async () => {
    const room = await renderView("initial");
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

    // One item renamed: the mark stays.
    act(() => room.show(marks, "renamed"));
    expect(marked()).toEqual(["Blur"]);

    // A bookmark changed: the mark goes.
    act(() => room.show(marks, "sharp"));
    expect(marked()).toEqual([]);

    fireEvent.click(screen.getByText("Sharp"));
    await act(async () => {});
    expect(marked()).toEqual(["Sharp"]);
    // The same bookmarks in a workspace that replaced the open one: the mark goes.
    act(() => room.show(marks, "replaced"));
    expect(marked()).toEqual([]);
  });

  it("renames, changes the kind of, removes and reviews through the backend", async () => {
    const room = await renderView("initial");
    const blur = marks.doc().workspace.bookmarks[0].bookmarks[1].id;
    const bookmark = (command: object) => ({ type: "bookmark", command });
    // Each change is the backend's answer to a Doc it started from; the next starts from that Doc again.
    const again = () => act(() => room.show(marks, "initial"));

    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    again();
    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Kind" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Question" }));
    again();
    fireEvent.contextMenu(screen.getByText("Blur"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("Blur");
    fireEvent.change(name, { target: { value: " Frame 90 " } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    again();
    fireEvent.click(screen.getAllByRole("button", { name: "To review" })[0]);
    await act(async () => {});

    expect(sent()).toEqual([
      bookmark({ type: "remove", id: blur }),
      bookmark({ type: "edit", id: blur, kind: "question" }),
      // The label it has without text is no text.
      bookmark({ type: "edit", id: blur, note: "" }),
      bookmark({ type: "setReviewed", item: marks.ids.v, reviewed: true }),
    ]);
  });

  it("exports every bookmark to the file the dialog chose", async () => {
    const pick = vi.fn(() => Promise.resolve("/tmp/marks.csv"));
    window.axDevilWindow = { pickExportFile: pick } as unknown as typeof window.axDevilWindow;
    const room = await renderView("initial");
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    const exported = () =>
      FakeSocket.latest.received.flatMap((message) =>
        message.type === "command" && message.cmd.type === "workspace" ? [message.cmd.command] : [],
      );
    await waitFor(() =>
      expect(exported()).toEqual([{ type: "exportBookmarks", path: "/tmp/marks.csv" }]),
    );
    expect(pick).toHaveBeenLastCalledWith("Untitled bookmarks.csv", "csv", undefined);
    act(() => room.show(marks, "saved"));
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() =>
      expect(pick).toHaveBeenLastCalledWith("Untitled bookmarks.csv", "csv", "/workspaces/"),
    );
    delete window.axDevilWindow;
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
    act(() => room.show(marks));
    expect(screen.getByTestId("view").textContent).toBe("workspace");
    act(() => room.show(marks, "replaced"));
    expect(screen.getByTestId("view").textContent).toBe("bookmarks");
    act(() => room.show(marks, "recovered"));
    expect(screen.getByTestId("view").textContent).toBe("workspace");
  });

  it("treats a workspace of another session at the same epoch as a replacement, and the same session again after a reload as none", async () => {
    // This window last saw epoch 1 of a session that is gone; the new session starts with a bookmarked workspace at 1.
    window.sessionStorage.setItem(
      "ax-devil.sidebarEpoch",
      JSON.stringify({ session: "gone", epoch: 1 }),
    );
    FakeSocket.launch.scenario = marks;
    FakeSocket.launch.state = "replaced";
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

  it("leaves a Guest following the presenter when a workspace with bookmarks replaces the one it joined", async () => {
    const host = FakeSocket.host("explore");
    host.show(marks, "replaced");
    function Guest() {
      useSidebarOnReplace();
      const { joinLink } = useSession();
      const [sidebar] = useView("sidebar");
      const following = useFollowing();
      return (
        <button type="button" onClick={() => joinLink(host.link)}>
          {`${sidebar.view} ${following ? "following" : "exploring"}`}
        </button>
      );
    }
    render(
      <SessionProvider>
        <Guest />
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

  it("keeps a Guest that chose Follow presenter following when the replacement's Doc arrives before its seat", async () => {
    const host = FakeSocket.host("explore");
    function Guest() {
      useSidebarOnReplace();
      const { joinLink, follow } = useSession();
      const [sidebar, setSidebar] = useView("sidebar");
      const following = useFollowing();
      return (
        <>
          <button type="button" onClick={() => joinLink(host.link)}>
            join
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
        <Guest />
      </SessionProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "join" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "explore" }));
    await waitFor(() => expect(host.seats[1].view).not.toBeNull());
    expect(screen.getByTestId("state").textContent).toBe("plugins exploring");

    // The backend coalesces: the replacement's Doc goes out, and only the latest seat follows it.
    const seats: Parameters<FakeSocket["emit"]>[0][] = [];
    const hold = vi.spyOn(FakeSocket.prototype, "emit").mockImplementation(function (
      this: FakeSocket,
      message,
    ) {
      if (message.type === "seat") seats.push(message);
      else this.onmessage?.({ data: JSON.stringify(message) });
    });
    fireEvent.click(screen.getByRole("button", { name: "follow" }));
    await act(async () => {});
    act(() => host.show(marks, "replaced"));
    await act(async () => {});
    hold.mockRestore();
    act(() => FakeSocket.latest.emit(seats[seats.length - 1]));
    await act(async () => {});
    expect(screen.getByTestId("state").textContent).toBe("workspace following");
  });

  it("bookmarks the frame on screen and brings the Bookmarks view up", async () => {
    const { session } = stub("ready");
    function Add() {
      const add = useAddBookmark(session, marks.ids.v, marks.ids.v);
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
        command: { type: "add", item: marks.ids.v, entry: marks.ids.v, frame: 7, timeUs: 280_000 },
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
