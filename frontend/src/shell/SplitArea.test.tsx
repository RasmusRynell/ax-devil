import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { scenario } from "../session/fakeScenario";
import { FakeSocket, type Room } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionStore } from "../session/store";
import { STORED_SEAT } from "../session/storedSeat";
import { SessionProvider } from "../session/session";
import type { ViewerProps } from "../viewers";
import { registerExport } from "../viewer/exportTargets";
import { PANE_MIME } from "../viewer/Pane";
import { SplitArea } from "./SplitArea";
import { useFrontViewer } from "../viewer/useViewers";

const mounted = vi.hoisted(() => [] as string[]);

vi.mock("../playback/VideoViewer", async () => {
  const { usePaneBudget } = await import("../playback/budget");
  return {
    VideoViewer: ({ content }: ViewerProps<"SeekableVideo">) => {
      const budget = usePaneBudget();
      useEffect(() => {
        mounted.push(content.id);
      }, [content.id]);
      return (
        <p>
          playing {content.id} <output aria-label={`budget ${content.id}`}>{budget}</output>
        </p>
      );
    },
  };
});

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  // A 400 by 300 area, and the same for every pane a drag is over.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 400,
    height: 300,
  } as DOMRect);
  mounted.length = 0;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** The area as the shell runs it: the owner's window gives a viewer to every open item. */
function Area() {
  useFrontViewer();
  return <SplitArea />;
}

const one = scenario("one-video");
const split = scenario("split");
const { a: itemA } = one.ids;

/** Shows `state` of `scene` in the owner's window. */
async function setup() {
  const store = new SessionStore();
  render(
    <SessionProvider store={store}>
      <Area />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  return {
    store,
    room,
    show: async (state: string, scene = one) => {
      act(() => room.show(scene, state));
      await waitFor(() => expect(store.getSnapshot().viewPending).toBe(false));
    },
  };
}

const pane = (name: string) => screen.getByRole("group", { name });

describe("split area", () => {
  it("shows an item as opening until it resolves, then its viewer", async () => {
    const { show } = await setup();
    await show("pending");
    expect(screen.getByRole("status").textContent).toBe("Opening a.mp4…");
    await show("initial");
    expect(screen.getByText(`playing ${itemA}`)).toBeTruthy();
  });

  it("keeps orphaned panes closed in two Owner seats, preserving the shared surviving pane", async () => {
    const { show, room, store } = await setup();
    // Another window has its own stored seat; attach it as an Owner to the same Presenter View.
    window.sessionStorage.removeItem(STORED_SEAT);
    const second = new SessionStore();
    render(
      <SessionProvider store={second}>
        <Area />
      </SessionProvider>,
    );
    await act(async () => {});
    await act(async () => {
      await second.attach(room.id);
    });
    expect(room.seats.filter((seat) => seat.owner && seat.socket)).toHaveLength(2);
    const scene = scenario("playlist-retirement");
    for (const state of ["single", "duplicates", "survivor"]) {
      await show(state, scene);
      const surviving = room.presenter.viewers[2];
      const overlays = room.presenter.overlays;
      await show(`${state}-resolved`, scene);
      await act(async () => {});
      if (state === "survivor") {
        expect(room.presenter.viewers).toEqual({ 2: surviving });
        expect(screen.getAllByRole("group", { name: "list" })).toHaveLength(2);
      } else {
        expect(room.presenter.viewers).toEqual({});
        expect(screen.queryByRole("group", { name: "list" })).toBeNull();
      }
      for (const seat of [store, second]) {
        expect(seat.getSnapshot().view.viewers).toEqual(room.presenter.viewers);
        expect(seat.getSnapshot().view.overlays).toEqual(overlays);
      }
    }
    // A local close stays closed while its view packet waits for the backend's retirement decision.
    await show("single", scene);
    await waitFor(() => expect(second.getSnapshot().viewPending).toBe(false));
    FakeSocket.sockets[0]!.defer = true;
    fireEvent.click(screen.getAllByRole("button", { name: "Close this pane" })[0]!);
    await act(async () => {});
    expect(store.getSnapshot().viewPending).toBe(true);
    expect(store.getSnapshot().view.viewers).toEqual({});
    expect(second.getSnapshot().view.viewers).toEqual(scene.view("single")!.viewers);
    act(() => room.show(scene, "single-resolved"));
    await act(async () => FakeSocket.sockets[0]!.flush());
    expect(screen.queryByRole("group", { name: "list" })).toBeNull();
  });

  it("says why an item failed, and where only to a seat that may read it", async () => {
    const { show } = await setup();
    await show("failed");
    expect(screen.getByRole("alert").textContent).toBe("File not found/data/a/a.mp4");
    // A playlist without entries is refused by the backend, which gives the reason.
    await show("empty-refused", scenario("playlist-groups"));
    expect(screen.getByRole("alert").textContent).toContain(
      "Playlists require at least one entry.",
    );
    cleanup();

    const room = FakeSocket.host("watch");
    room.show(one, "failed");
    room.publish({ sidebar: room.presenter.sidebar, ...viewing(itemA) });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <Area />
      </SessionProvider>,
    );
    expect((await screen.findByRole("alert")).textContent).toBe("The file was not found.");
  });

  it("offers to reload a viewer whose files changed, once per change, and reloading resolves the item", async () => {
    const { show } = await setup();
    const offer = () => screen.queryByText("Source changed, reload current viewer?");
    await show("initial");
    expect(offer()).toBeNull();

    await show("changed-1");
    expect(offer()).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(offer()).toBeNull();
    await show("changed-1");
    expect(offer()).toBeNull();
    await show("changed-2");
    expect(offer()).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "resolve", item: itemA } },
      }),
    );
  });

  it("says why a reload failed above the viewer it left in place, until dismissed, and again for the next failure", async () => {
    const { show } = await setup();
    await show("initial");
    await show("reload-failed-1");
    expect(screen.getByRole("alert").textContent).toBe("Could not reload: File not found");
    expect(screen.getByText(`playing ${itemA}`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
    await show("reload-failed-1");
    expect(screen.queryByRole("alert")).toBeNull();
    await show("reload-failed-2");
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("gives eight open items a pane and closes the ninth again", async () => {
    const { show } = await setup();
    await show("nine-open", split);
    expect(screen.getAllByRole("group")).toHaveLength(8);
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "closeViewer", item: split.ids.i } },
      }),
    );
  });

  it("gives each pane an equal share of the window's 512 MiB of frames, redivided as panes come and go", async () => {
    const { show } = await setup();
    const budget = (key: string) => screen.getByLabelText(`budget ${split.ids[key]}`).textContent;
    await show("a-open", split);
    expect(budget("a")).toBe(String(512 * 1024 * 1024));
    await show("abc-open", split);
    expect(budget("a")).toBe(String(Math.floor((512 * 1024 * 1024) / 3)));
    fireEvent.click(within(pane("c.mp4")).getByRole("button", { name: "Close this pane" }));
    expect(budget("a")).toBe(String(256 * 1024 * 1024));
  });

  it("gives a pane's header the row's menu without Edit, exporting from that pane's own viewer, and Remove closes the item", async () => {
    const { show } = await setup();
    await show("initial");
    const id = Number(pane("a.mp4").getAttribute("data-cursor-pane"));
    // Another viewer of the same item, registered first: the header exports the one of its own pane.
    const other = vi.fn();
    const own = vi.fn();
    const leave = [
      registerExport(id + 1, itemA, itemA, other),
      registerExport(id, itemA, itemA, own),
    ];
    fireEvent.contextMenu(within(pane("a.mp4")).getByText("a.mp4"));
    expect(screen.getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual([
      "Open",
      "Open to the Side",
      "Information",
      "Export video",
      "Remove",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Export video" }));
    expect([own.mock.calls.length, other.mock.calls.length]).toEqual([1, 0]);

    fireEvent.contextMenu(within(pane("a.mp4")).getByText("a.mp4"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "remove", item: itemA } },
      }),
    );
    leave.forEach((release) => release());
  });

  it("says in its pane why a header action failed, until dismissed, Escape or the next menu", async () => {
    const { show } = await setup();
    await show("initial");
    const header = () => pane("a.mp4").querySelector("[draggable]") as HTMLElement;
    const alert = () => within(pane("a.mp4")).queryByRole("alert");
    FakeSocket.refuseContent = "NotAllowed";
    const fail = async () => {
      fireEvent.contextMenu(header());
      fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
      await within(pane("a.mp4")).findByRole("alert");
    };
    await fail();
    // Inside the pane, above its viewer, with the reason and a way to dismiss it.
    expect(alert()?.textContent).toContain("Your role in this session cannot do that.");
    fireEvent.click(within(alert() as HTMLElement).getByRole("button", { name: "Dismiss" }));
    expect(alert()).toBeNull();

    await fail();
    expect(alert()).not.toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(alert()).toBeNull();

    await fail();
    fireEvent.contextMenu(header());
    expect(alert()).toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    FakeSocket.refuseContent = null;
  });

  it("acts from a header on the entry the pane shows: with a whole first playlist hidden, one of the second", async () => {
    const { show } = await setup();
    const groups = scenario("playlist-groups");
    await show("p-hidden", groups);
    const id = Number(pane("list").getAttribute("data-cursor-pane"));
    const shownExport = vi.fn();
    const leave = registerExport(id, groups.ids.list, groups.ids["list/C"], shownExport);
    fireEvent.contextMenu(pane("list").querySelector("[draggable]") as HTMLElement);
    // The row of C, which is not hidden: Hide, not Unhide; and C's own export runs.
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toContain("Hide");
    fireEvent.click(screen.getByRole("menuitem", { name: "Export video" }));
    expect(shownExport).toHaveBeenCalledTimes(1);
    leave();
  });

  it("keeps a playlist header's menu while its entry's playback is not ready, with Export disabled", async () => {
    const { show } = await setup();
    await show("initial", scenario("playlist"));
    // No viewer registered an export: the entry's media is still opening or failed.
    fireEvent.contextMenu(pane("list").querySelector("[draggable]") as HTMLElement);
    const names = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(names).toEqual(
      expect.arrayContaining(["Open", "Open to the Side", "Information", "Export video", "Remove"]),
    );
    const exportItem = screen.getByRole("menuitem", { name: "Export video" }) as HTMLButtonElement;
    expect([exportItem.disabled, exportItem.title]).toEqual([true, "Open it first"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Information" }));
    const information = screen.getByRole("dialog", { name: "list" });
    expect(
      within(information)
        .getAllByRole("row")
        .map((row) => row.textContent),
    ).toContain("entries4");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.contextMenu(pane("list").querySelector("[draggable]") as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "remove", item: scenario("playlist").ids.list } },
      }),
    );
  });

  it("moves a dragged pane to the side it is dropped on without restarting its viewer or unpinning it", async () => {
    const { show } = await setup();
    await show("ab-open", split);
    fireEvent.click(within(pane("a.mp4")).getByRole("button", { name: /^Pin:/ }));
    const left = (name: string) => pane(name).style.left;
    expect([left("a.mp4"), left("b.mp4")]).toEqual(["0px", "200px"]);

    const types: string[] = [];
    const data: Record<string, string> = {};
    const dataTransfer = {
      types,
      setData: (type: string, value: string) => {
        types.push(type);
        data[type] = value;
      },
      getData: (type: string) => data[type],
      effectAllowed: "",
      dropEffect: "",
    };
    const header = pane("a.mp4").querySelector("[draggable]") as HTMLElement;
    fireEvent.dragStart(header, { dataTransfer });
    expect(types).toEqual([PANE_MIME]);
    const at = { dataTransfer, clientX: 300, clientY: 150 };
    fireEvent.dragEnter(pane("b.mp4"), at);
    fireEvent.dragOver(pane("b.mp4"), at);
    fireEvent.drop(pane("b.mp4"), at);

    // Dropped on the right half of b: a now sits right of it.
    expect([left("a.mp4"), left("b.mp4")]).toEqual(["200px", "0px"]);
    expect(within(pane("a.mp4")).getByRole("button", { name: /^Unpin:/ })).toBeTruthy();
    expect(mounted).toEqual([split.ids.a, split.ids.b]);
  });
});
