import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ItemRecipe } from "../generated/api";
import {
  entry,
  FakeSocket,
  playlist,
  playlists,
  resolved,
  video,
  type Room,
} from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import type { ViewerProps } from "../viewers";
import { registerExport } from "./exportTargets";
import { PANE_MIME } from "./Pane";
import { SplitArea } from "./SplitArea";
import { useFrontViewer } from "./useViewers";

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

async function setup() {
  render(
    <SessionProvider>
      <Area />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  return {
    room,
    send: (...items: Parameters<typeof video>[]) =>
      act(() => room.setItems(items.map((item) => video(...item)))),
  };
}

const pane = (name: string) => screen.getByRole("group", { name });

describe("split area", () => {
  it("shows an item as opening until it resolves, then its viewer", async () => {
    const { send } = await setup();
    send(["a", true, { status: "Pending" }]);
    expect(screen.getByRole("status").textContent).toBe("Opening a.mp4…");
    send(["a", true, resolved("a")]);
    expect(screen.getByText("playing content-a")).toBeTruthy();
  });

  it("says why an item failed, and where only when the backend told it", async () => {
    const { send } = await setup();
    const failed = { status: "Failed" as const, code: "FileNotFound" as const, params: {} };
    send(["a", true, { ...failed, reason: "no such file", location: "/clips/a.mp4" }]);
    expect(screen.getByRole("alert").textContent).toBe("no such file/clips/a.mp4");
    send(["a", true, failed]);
    expect(screen.getByRole("alert").textContent).toBe("The file was not found.");
  });

  it("offers to reload a viewer whose files changed, once per change, and reloading resolves the item", async () => {
    const { room, send } = await setup();
    const offer = () => screen.queryByText("Source changed, reload current viewer?");
    send(["a", true, resolved("a")]);
    expect(offer()).toBeNull();

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

  it("says why a reload failed above the viewer it left in place, until dismissed, and again for the next failure", async () => {
    const { room, send } = await setup();
    send(["a", true, resolved("a")]);
    const fail = (attempt: number, reason: string) =>
      act(() =>
        room.setItems([
          {
            ...video("a"),
            reload_failure: { attempt, error: { code: "FileNotFound", params: {}, reason } },
          },
        ]),
      );
    fail(3, "gate.mp4 is gone");
    expect(screen.getByRole("alert").textContent).toBe("Could not reload: gate.mp4 is gone");
    expect(screen.getByText("playing content-a")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
    fail(3, "gate.mp4 is gone");
    expect(screen.queryByRole("alert")).toBeNull();
    fail(5, "gate.mp4 is gone");
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("gives eight open items a pane and closes the ninth again", async () => {
    const { send } = await setup();
    const ids = Array.from({ length: 9 }, (_, n) => `v${n + 1}`);
    send(...ids.map((id) => [id, true, resolved(id)] as Parameters<typeof video>));
    expect(screen.getAllByRole("group")).toHaveLength(8);
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: { type: "content", command: { type: "closeViewer", item: "v9" } },
      }),
    );
  });

  it("gives each pane an equal share of the window's 512 MiB of frames, redivided as panes come and go", async () => {
    const { send } = await setup();
    const budget = (id: string) => screen.getByLabelText(`budget content-${id}`).textContent;
    send(["a", true, resolved("a")]);
    expect(budget("a")).toBe(String(512 * 1024 * 1024));
    send(["a", true, resolved("a")], ["b", true, resolved("b")], ["c", true, resolved("c")]);
    expect(budget("a")).toBe(String(Math.floor((512 * 1024 * 1024) / 3)));
    fireEvent.click(within(pane("c.mp4")).getByRole("button", { name: "Close this pane" }));
    expect(budget("a")).toBe(String(256 * 1024 * 1024));
  });

  it("gives a pane's header the row's menu without Edit, exporting from that pane's own viewer, and Remove closes the item", async () => {
    const { room } = await setup();
    const recipe: ItemRecipe = { type: "Video", path: "/a.mp4", label: null, overlays: [] };
    act(() => room.setItems([{ ...video("a"), recipe }]));
    const id = Number(pane("a.mp4").getAttribute("data-cursor-pane"));
    // Another viewer of the same item, registered first: the header exports the one of its own pane.
    const other = vi.fn();
    const own = vi.fn();
    const leave = [
      registerExport(id + 1, "a", "content-a", other),
      registerExport(id, "a", "content-a", own),
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
      expect.objectContaining({ cmd: { type: "content", command: { type: "remove", item: "a" } } }),
    );
    leave.forEach((release) => release());
  });

  it("says in its pane why a header action failed, until dismissed, Escape or the next menu", async () => {
    const { room } = await setup();
    const recipe: ItemRecipe = { type: "Video", path: "/a.mp4", label: null, overlays: [] };
    act(() => room.setItems([{ ...video("a"), recipe }]));
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
    const { room } = await setup();
    act(() =>
      room.setItems([
        playlists("p", [
          ["one", [entry("a1")]],
          ["two", [entry("b1")]],
        ]),
      ]),
    );
    act(() => room.setHidden({ entry: "a1" }, true));
    const id = Number(pane("p").getAttribute("data-cursor-pane"));
    const shownExport = vi.fn();
    const leave = registerExport(id, "p", "b1", shownExport);
    fireEvent.contextMenu(pane("p").querySelector("[draggable]") as HTMLElement);
    // The row of b1, which is not hidden: Hide, not Unhide; and b1's own export runs.
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toContain("Hide");
    fireEvent.click(screen.getByRole("menuitem", { name: "Export video" }));
    expect(shownExport).toHaveBeenCalledTimes(1);
    leave();
  });

  it("keeps a playlist header's menu while its entry's playback is not ready, with Export disabled", async () => {
    const { room } = await setup();
    act(() => room.setItems([playlist("p", [entry("p/a", "A"), entry("p/b", "B")])]));
    // No viewer registered an export: the entry's media is still opening or failed.
    fireEvent.contextMenu(pane("p").querySelector("[draggable]") as HTMLElement);
    const names = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(names).toEqual(
      expect.arrayContaining(["Open", "Open to the Side", "Information", "Export video"]),
    );
    const exportItem = screen.getByRole("menuitem", { name: "Export video" }) as HTMLButtonElement;
    expect([exportItem.disabled, exportItem.title]).toEqual([true, "Open it first"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Information" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("moves a dragged pane to the side it is dropped on without restarting its viewer or unpinning it", async () => {
    const { send } = await setup();
    send(["a", true, resolved("a")], ["b", true, resolved("b")]);
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
    expect(mounted).toEqual(["content-a", "content-b"]);
  });
});
