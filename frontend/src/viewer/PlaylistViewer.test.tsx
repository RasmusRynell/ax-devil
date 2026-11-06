import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { EntryInfo, HiddenTarget, ItemState } from "../generated/api";
import { entry, FakeSocket, playlist, playlists, type Room } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionProvider, useDoc, useView } from "../session/session";
import type { VideoViewerProps } from "../playback/VideoViewer";
import { NO_ACTIONS, ViewerActionsContext, useViewerActions, type ViewerActions } from "./actions";
import { hiddenOf, NOTHING_HIDDEN } from "./hidden";
import { adjacentEntry, PlaylistViewer } from "./PlaylistViewer";

const ownActions: ViewerActions = { "play-pause": () => undefined };

// An entry's video viewer that says which entry it was given.
vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: ({ entry: shown }: VideoViewerProps) => {
    useViewerActions(ownActions);
    return <p>playing {shown.label}</p>;
  },
}));

const ENTRIES = ["A", "B", "C", "D"].map((label) => entry(`list/${label}`, label));

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const hiding = (...labels: string[]): HiddenTarget[] =>
  labels.map((label) => ({ entry: `list/${label}` }));

describe("adjacent entry", () => {
  it("skips hidden entries and stops at the ends", () => {
    const hidden = hiddenOf(
      hiding("B", "C").map((target) => ({
        key: target.entry,
        item: "list",
        kind: "entry" as const,
        label: target.entry,
        status: { status: "Ready" as const },
        hidden: true,
        entry: target.entry,
        warnings: [],
      })),
    );
    expect(adjacentEntry(ENTRIES, hidden, 0, 1)?.label).toBe("D");
    expect(adjacentEntry(ENTRIES, hidden, 3, -1)?.label).toBe("A");
    expect(adjacentEntry(ENTRIES, hidden, 0, -1)).toBeUndefined();
    expect(adjacentEntry(ENTRIES, NOTHING_HIDDEN, 3, 1)).toBeUndefined();
  });
});

/** The playlist's viewer, once the Doc has the item, with the commands it offers kept in `registry`. */
function Host() {
  const resolution = useDoc()?.workspace.items.find((item) => item.id === "list")?.resolution;
  const [viewers] = useView("viewers");
  return (
    <>
      {resolution?.status === "Resolved" && resolution.content.kind === "Playlist" && (
        <PlaylistViewer item="list" viewer={1} content={resolution.content} />
      )}
      <div data-testid="view">{JSON.stringify(viewers[1] ?? null)}</div>
    </>
  );
}

const registry = { current: NO_ACTIONS };
const offer = (next: ViewerActions | ((now: ViewerActions) => ViewerActions)) => {
  registry.current = typeof next === "function" ? next(registry.current) : next;
};

/** Shows the playlist in an owner's window; `entries` and `hidden` are what the Doc holds. */
async function mount({
  hidden = [],
  entries = ENTRIES,
  shown,
  item,
}: { hidden?: HiddenTarget[]; entries?: EntryInfo[]; shown?: string; item?: ItemState } = {}) {
  registry.current = NO_ACTIONS;
  render(
    <SessionProvider>
      <ViewerActionsContext.Provider value={offer}>
        <Host />
      </ViewerActionsContext.Provider>
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  room.hidden = hidden;
  act(() => {
    room.setItems([item ?? playlist("list", entries)]);
    // The window shows the playlist in its viewer 1, on the entry named or the first one shown.
    room.publish({
      sidebar: room.presenter.sidebar,
      ...viewing(["list", shown ? { entry: shown } : {}]),
    });
  });
  const run = (id: string) => act(() => registry.current[id]?.());
  const view = () => JSON.parse(screen.getByTestId("view").textContent ?? "null");
  return { room, run, view };
}

describe("playlist viewer", () => {
  it("opens the entry the view names, else the first that is not hidden", async () => {
    await mount({ hidden: hiding("A") });
    expect(screen.getByText("playing B")).toBeTruthy();
    cleanup();
    await mount({ shown: "list/C" });
    expect(screen.getByText("playing C")).toBeTruthy();
  });

  it("shows an entry the view names even when it is hidden, as when the tree opened it", async () => {
    await mount({ hidden: hiding("C"), shown: "list/C" });
    expect(screen.getByText("playing C")).toBeTruthy();
    expect(screen.getByLabelText("Entry").textContent).toBe("3 / 4");
  });

  it("moves between entries with its buttons and the commands, skipping hidden ones, and starts each from its own beginning", async () => {
    const { run, view } = await mount({ hidden: hiding("B") });
    expect(screen.getByText("playing A")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous entry" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Next entry" }));
    expect(screen.getByText("playing C")).toBeTruthy();
    expect(screen.getByLabelText("Entry").textContent).toBe("3 / 4");
    // The view names the entry and no clock: the one that was playing is not the new entry's.
    expect(view()).toEqual({ item: "list", pinned: false, entry: "list/C" });
    run("next-entry");
    expect(screen.getByText("playing D")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next entry" })).toHaveProperty("disabled", true);
    run("previous-entry");
    run("previous-entry");
    expect(screen.getByText("playing A")).toBeTruthy();
  });

  it("steps only within the playlist holding the entry on screen, and names it", async () => {
    const both = playlists("list", [
      ["P", ENTRIES.slice(0, 2)],
      ["Q", ENTRIES.slice(2)],
    ]);
    await mount({ item: both, shown: "list/C" });
    expect(screen.getByText("list / Q")).toBeTruthy();
    expect(screen.getByLabelText("Entry").textContent).toBe("1 / 2");
    expect(screen.getByRole("button", { name: "Previous entry" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Next entry" }));
    expect(screen.getByText("playing D")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next entry" })).toHaveProperty("disabled", true);
  });

  it("offers no command where there is no entry to go to, and keeps the entry viewer's own", async () => {
    await mount();
    expect(registry.current["previous-entry"]).toBeUndefined();
    expect(registry.current["next-entry"]).toBeTypeOf("function");
    expect(registry.current["play-pause"]).toBe(ownActions["play-pause"]);
  });

  it("picks an entry from its menu, which lists the entries that are not hidden", async () => {
    await mount({ hidden: hiding("C") });
    fireEvent.click(screen.getByRole("button", { name: "Entry: A" }));
    expect(screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)).toEqual([
      "A",
      "B",
      "D",
    ]);
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "D" }));
    expect(screen.getByText("playing D")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves the entry on screen for the next one that is not hidden when it is hidden, else the previous", async () => {
    const { room, run } = await mount({ shown: "list/B" });
    act(() => room.setHidden({ entry: "list/C" }, true));
    expect(screen.getByText("playing B")).toBeTruthy();
    act(() => room.setHidden({ entry: "list/B" }, true));
    expect(screen.getByText("playing D")).toBeTruthy();
    act(() => room.setHidden({ entry: "list/D" }, true));
    expect(screen.getByText("playing A")).toBeTruthy();
    // Nowhere to go: the entry stays, and so do the buttons, with nothing to step to.
    act(() => room.setHidden({ entry: "list/A" }, true));
    expect(screen.getByText("playing A")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next entry" })).toHaveProperty("disabled", true);
    run("next-entry");
    expect(screen.getByText("playing A")).toBeTruthy();
  });

  it("shows the entry the presenter moves to", async () => {
    const room = FakeSocket.host("watch");
    room.setItems([playlist("list", ENTRIES)]);
    room.publish({ sidebar: room.presenter.sidebar, ...viewing(["list", { entry: "list/A" }]) });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <Host />
      </SessionProvider>,
    );
    await act(async () => {});
    expect(screen.getByText("playing A")).toBeTruthy();
    act(() =>
      room.publish({ sidebar: room.presenter.sidebar, ...viewing(["list", { entry: "list/C" }]) }),
    );
    expect(screen.getByText("playing C")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers no way to move between entries when there is one, and shows the first when all are hidden", async () => {
    await mount({ entries: ENTRIES.slice(0, 1) });
    expect(screen.getByText("playing A")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Next entry" })).toBeNull();
    expect(screen.queryByLabelText("Entry")).toBeNull();
    cleanup();
    // Of several playlists, the one with a single entry still says which it is.
    await mount({
      item: playlists("list", [
        ["P", ENTRIES.slice(0, 1)],
        ["Q", ENTRIES.slice(1)],
      ]),
    });
    expect(screen.getByText("list / P")).toBeTruthy();
    expect(screen.queryByLabelText("Entry")).toBeNull();
    cleanup();
    await mount({ hidden: hiding("A", "B", "C", "D") });
    expect(screen.getByText("playing A")).toBeTruthy();
  });

  it("says why there is nothing to show", async () => {
    await mount({ entries: [] });
    expect(screen.getByRole("status").textContent).toBe("This playlist has no entries");
  });
});
