import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { Grant, NetworkRow, NetworkSnapshot } from "../generated/api";
import type { AddSeed } from "../live/cameras";
import type { SettingsApi } from "../settings";
import { scenario } from "../session/fakeScenario";
import { FakeSocket, type Room } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionProvider } from "../session/session";
import { KeptStateProvider } from "../kit/keptState";
import { LiveView } from "./LiveView";

const found = (address: string, sign_in: NetworkRow["sign_in"]): NetworkRow => ({
  address,
  scheme: "https",
  port: 8443,
  identity: { model: "Q1656", serial: "S9", firmware: "11.8.1", product_type: "Box Camera" },
  uptime_s: 90_000,
  sign_in,
});

let SNAPSHOT: NetworkSnapshot;
const FOUND: NetworkSnapshot = {
  scan: 1,
  scanning: false,
  failure: null,
  rows: [
    found("10.0.0.2", { state: "SignedIn", login_index: 0, architecture: "", soc: "" }),
    found("10.0.0.3", { state: "Refused" }),
  ],
};

const streams = scenario("streams");

const fetched = vi.fn();
let refuseScan = false;
let loginRefused = false;
// Holds the answer of the next scan request until the test releases it.
let holdScan: Promise<void> | null = null;
// The number of the scan the backend says it started.
let startedScan = 1;
// Whether the snapshot stream leaves out the snapshot it opens with.
let withhold = false;
// The open snapshot stream, for a test to push scans' snapshots into.
let follow: ((snapshot: NetworkSnapshot) => void) | null = null;
// Holds the opening of the next snapshot stream until the test releases it.
let holdStream: Promise<void> | null = null;
// The answers of the next scan requests, in order; later requests are answered by `startedScan`.
let scanReplies: Promise<Response>[] = [];
// Ends the open snapshot stream, as a backend going away does.
let hangUp: () => void = () => {};

beforeEach(() => {
  SNAPSHOT = FOUND;
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      fetched(url, init?.body);
      if (url === "/api/network/scan" && scanReplies.length > 0)
        return scanReplies.shift() as Promise<Response>;
      if (url === "/api/network/scan" && holdScan) await holdScan;
      if (url === "/api/network/scan" && refuseScan) return new Response("{}", { status: 422 });
      if (url === "/api/live/check" && loginRefused)
        return new Response(JSON.stringify(found("10.0.0.2", { state: "Refused" })));
      if (url === "/api/network/scan") return new Response(JSON.stringify({ scan: startedScan }));
      if (url !== "/api/network") return new Response(null, { status: 204 });
      await holdStream;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            const write = (snapshot: NetworkSnapshot) =>
              controller.enqueue(new TextEncoder().encode(`${JSON.stringify(snapshot)}\n`));
            if (!withhold) write(SNAPSHOT);
            follow = write;
            hangUp = () => controller.close();
          },
        }),
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetched.mockClear();
  refuseScan = false;
  loginRefused = false;
  follow = null;
  holdStream = null;
  scanReplies = [];
  holdScan = null;
  startedScan = 1;
  withhold = false;
  window.sessionStorage.clear();
  FakeSocket.reset();
});

async function renderView(
  state?: string,
  onAdd: (seed: AddSeed) => void = () => {},
  {
    grants = ["files", "preferences"],
    save,
  }: { grants?: Grant[]; save?: SettingsApi["save"] } = {},
) {
  render(
    <GrantsContext value={grants}>
      <FakeSettings save={save}>
        <SessionProvider>
          <KeptStateProvider>
            <LiveView onAdd={onAdd} />
          </KeptStateProvider>
        </SessionProvider>
      </FakeSettings>
    </GrantsContext>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  if (state) act(() => room.show(streams, state));
  return room;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );

describe("found cameras", () => {
  it("offers Add only after the scan signed in, and adds and starts the stream by address", async () => {
    const added = vi.fn();
    await renderView(undefined, added);
    await screen.findByText("10.0.0.3");
    expect(screen.getAllByRole("button", { name: "Add" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(sent()).toEqual([
        { type: "addItems", items: [{ type: "Live", found: "10.0.0.2" }], open: true },
      ]),
    );
    expect(added).not.toHaveBeenCalled();
  });

  it("opens a camera's info on a click and finds it by what the scan learned", async () => {
    await renderView();
    fireEvent.click(await screen.findByText("10.0.0.3"));
    expect(screen.getByText("10.0.0.3 · Box Camera · Firmware 11.8.1")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search" }), {
      target: { value: "no such" },
    });
    expect(screen.getByText("No matches")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search" }), {
      target: { value: "S9" },
    });
    expect(screen.queryByText("No matches")).toBeNull();
  });
});

describe("scanning", () => {
  it("says so in the scan settings when the range cannot be scanned", async () => {
    refuseScan = true;
    await renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Rescan" }));
    expect(await screen.findByText("Invalid IP range")).toBeTruthy();
  });

  const scansStarted = () => fetched.mock.calls.filter(([url]) => url === "/api/network/scan");
  const emit = (snapshot: NetworkSnapshot) =>
    act(async () => {
      follow?.(snapshot);
    });

  it("starts one scan however often Enter is pressed, showing the cameras as they arrive", async () => {
    SNAPSHOT = { scan: 0, scanning: false, rows: [], failure: null };
    await renderView();
    const range = await screen.findByRole("textbox", { name: "IP range" });
    fireEvent.keyDown(range, { key: "Enter" });
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(1);
    expect((screen.getByRole("button", { name: "Rescan" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    await emit({ ...FOUND, scan: 1, scanning: true, rows: FOUND.rows.slice(0, 1) });
    expect(await screen.findByText("10.0.0.2")).toBeTruthy();
    expect(screen.getByText("Scanning…")).toBeTruthy();
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(1);

    await emit(FOUND);
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Rescan" }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    expect(screen.queryByText("Scanning…")).toBeNull();
  });

  it("holds the next scan back until a snapshot of the scan started is shown", async () => {
    let answer = () => {};
    holdScan = new Promise((resolve) => (answer = resolve));
    startedScan = 3;
    withhold = true;
    await renderView();
    const range = await screen.findByRole("textbox", { name: "IP range" });
    fireEvent.keyDown(range, { key: "Enter" });
    await emit(idle(2));
    fireEvent.keyDown(range, { key: "Enter" });
    await act(async () => answer());
    await emit(idle(2));
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(1);
    expect(rescanButton().disabled).toBe(true);
    await emit(idle(3));
    expect(rescanButton().disabled).toBe(false);
  });

  const rescanButton = () => screen.getByRole("button", { name: "Rescan" }) as HTMLButtonElement;
  const idle = (scan: number): NetworkSnapshot => ({
    scan,
    scanning: false,
    rows: [],
    failure: null,
  });
  // The stream drops and opens again, to a backend whose scan is numbered `scan`.
  const reconnect = async (scan: number) => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    SNAPSHOT = idle(scan);
    await act(async () => {
      hangUp();
      await vi.advanceTimersByTimeAsync(5000);
    });
    vi.useRealTimers();
  };

  it("lets a scan start again once the scan has run or the backend restarted, counting from zero", async () => {
    SNAPSHOT = idle(4);
    await renderView();
    const range = await screen.findByRole("textbox", { name: "IP range" });
    startedScan = 5;
    fireEvent.keyDown(range, { key: "Enter" });
    await emit(idle(5));
    expect(rescanButton().disabled).toBe(false);
    await reconnect(1);
    expect(rescanButton().disabled).toBe(false);

    // The backend answered scan 6, then restarted before any snapshot of it was shown.
    startedScan = 6;
    fireEvent.keyDown(range, { key: "Enter" });
    await act(async () => {});
    expect(rescanButton().disabled).toBe(true);
    await reconnect(1);
    expect(rescanButton().disabled).toBe(false);
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(3);
  });

  it("starts no scan before the snapshot stream has opened, or while it is down", async () => {
    let open = () => {};
    holdStream = new Promise((resolve) => (open = resolve));
    await renderView();
    const range = await screen.findByRole("textbox", { name: "IP range" });
    expect(rescanButton().disabled).toBe(true);
    fireEvent.keyDown(range, { key: "Enter" });
    holdStream = null;
    await act(async () => open());
    await waitFor(() => expect(rescanButton().disabled).toBe(false));

    holdStream = new Promise((resolve) => (open = resolve));
    await reconnect(1);
    expect(rescanButton().disabled).toBe(true);
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(0);
    holdStream = null;
    await act(async () => open());
    await waitFor(() => expect(rescanButton().disabled).toBe(false));
  });

  it.each([
    ["answers late", () => new Response(JSON.stringify({ scan: 2 }))],
    ["is refused late", () => new Response("{}", { status: 422 })],
  ])("keeps the guard of a newer press when an older one %s", async (_, late) => {
    let answer: (reply: Response) => void = () => {};
    scanReplies = [new Promise((resolve) => (answer = resolve))];
    startedScan = 7;
    SNAPSHOT = idle(1);
    await renderView();
    const range = await screen.findByRole("textbox", { name: "IP range" });
    fireEvent.keyDown(range, { key: "Enter" });
    await reconnect(1);
    fireEvent.keyDown(range, { key: "Enter" });
    await act(async () => {});
    await act(async () => answer(late()));
    await emit(idle(2));
    expect(rescanButton().disabled).toBe(true);
    fireEvent.keyDown(range, { key: "Enter" });
    expect(scansStarted()).toHaveLength(2);
    await emit(idle(7));
    expect(rescanButton().disabled).toBe(false);
    expect(screen.queryByText("Invalid IP range")).toBeNull();
  });

  it("says a scan failed, with the reason, and keeps the cameras it found", async () => {
    SNAPSHOT = { scan: 0, scanning: false, rows: [], failure: null };
    await renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Rescan" }));
    await emit({ ...FOUND, scan: 1, failure: "The scan stopped unexpectedly." });
    expect(await screen.findByText("Scan failed")).toBeTruthy();
    expect(screen.getByTitle("The scan stopped unexpectedly.")).toBeTruthy();
    expect(screen.getByText("10.0.0.2")).toBeTruthy();
  });

  it("takes the sign-in box for the scans it starts without saving it", async () => {
    const save = vi.fn(() => Promise.resolve(null));
    await renderView(undefined, undefined, { grants: ["files", "configuration"], save });
    fireEvent.click(await screen.findByRole("checkbox", { name: /sign in to found cameras/ }));
    fireEvent.click(screen.getByRole("button", { name: "Rescan" }));
    expect(JSON.parse(scansStarted()[0][1] as string)).toEqual({ sign_in: false });
    expect(save).not.toHaveBeenCalled();
  });

  it("returns the sign-in box to the saved setting whenever that changes", async () => {
    const page = (signIn: boolean) => (
      <GrantsContext value={["files", "configuration"]}>
        <FakeSettings
          state={{
            ...DEFAULT_SETTINGS,
            values: { ...DEFAULT_SETTINGS.values, "scan.sign_in": signIn },
          }}
        >
          <SessionProvider>
            <KeptStateProvider>
              <LiveView onAdd={() => {}} />
            </KeptStateProvider>
          </SessionProvider>
        </FakeSettings>
      </GrantsContext>
    );
    const { rerender } = render(page(true));
    const box = async () =>
      (await screen.findByRole("checkbox", {
        name: /sign in to found cameras/,
      })) as HTMLInputElement;
    expect((await box()).checked).toBe(true);
    fireEvent.click(await box());
    expect((await box()).checked).toBe(false);
    rerender(page(false));
    expect((await box()).checked).toBe(false);
    rerender(page(true));
    expect((await box()).checked).toBe(true);

    rerender(page(false));
    fireEvent.click(await box());
    expect((await box()).checked).toBe(true);
    rerender(page(true));
    rerender(page(false));
    expect((await box()).checked).toBe(false);
  });
});

describe("workspace streams", () => {
  it("adds a stream of a new camera from the Live caption", async () => {
    const added = vi.fn();
    await renderView(undefined, added);
    fireEvent.click(await screen.findByRole("button", { name: "Add stream" }));
    expect(added).toHaveBeenCalledWith({});
  });

  it("starts and stops a stream by opening and closing its viewer", async () => {
    await renderView("initial");
    fireEvent.click(await screen.findByRole("button", { name: "Start stream" }));
    await waitFor(() =>
      expect(sent()).toContainEqual({ type: "openViewer", item: streams.ids.lobby }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Stop stream" }));
    await waitFor(() =>
      expect(sent()).toContainEqual({ type: "closeViewer", item: streams.ids.lobby }),
    );
  });

  it("starts a stream beside the open viewers, never in place of the one in front", async () => {
    const room = await renderView("dock-open");
    act(() =>
      room.publish({ sidebar: { view: "live", open: true }, ...viewing(streams.ids.dock) }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Start stream" }));
    await waitFor(() => expect(Object.keys(room.presenter.viewers)).toHaveLength(2));
    expect(Object.values(room.presenter.viewers).map((viewer) => viewer?.item)).toEqual([
      streams.ids.dock,
      streams.ids.lobby,
    ]);
  });
});

describe("stream menu", () => {
  it("offers Information and Remove beside Start, and removes the item", async () => {
    await renderView("initial");
    fireEvent.contextMenu(await screen.findByText("Video only"));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Start stream",
      "Information",
      "Edit",
      "Remove",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Information" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("device API protocol");
    expect(dialog.textContent).toContain("HTTPS");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.contextMenu(await screen.findByText("Video only"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    await waitFor(() => expect(sent()).toContainEqual({ type: "remove", item: streams.ids.lobby }));
  });

  it("takes Start from a stream whose camera refuses the login saved with it", async () => {
    loginRefused = true;
    await renderView("initial");
    expect(await screen.findByText("The camera refused the login saved with it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start stream" })).toBeNull();
  });
});

describe("keyboard", () => {
  const focusTree = async (name: string) => {
    const tree = await screen.findByRole("tree", { name });
    act(() => tree.focus());
    return tree;
  };
  const activeLabel = (tree: HTMLElement) =>
    document.getElementById(tree.getAttribute("aria-activedescendant") ?? "")?.textContent ?? "";

  it("starts on the first row, opens with Enter and removes with Delete", async () => {
    await renderView("initial");
    const tree = await focusTree("Live");
    expect(activeLabel(tree)).toContain("Lobby");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(activeLabel(tree)).toContain("Video only");
    fireEvent.keyDown(tree, { key: "Enter" });
    await waitFor(() =>
      expect(sent()).toContainEqual({ type: "openViewer", item: streams.ids.lobby }),
    );
    fireEvent.keyDown(tree, { key: "Delete" });
    await waitFor(() => expect(sent()).toContainEqual({ type: "remove", item: streams.ids.lobby }));
  });

  it("leaves Enter on a button inside a row to the button", async () => {
    await renderView("initial");
    const tree = await focusTree("Live");
    fireEvent.keyDown(await screen.findByRole("button", { name: "Start stream" }), {
      key: "Enter",
    });
    expect(sent()).toEqual([]);
    expect(tree.getAttribute("aria-activedescendant")).not.toBeNull();
  });

  it("gives the focus back to the tree when a row's menu closes", async () => {
    await renderView("initial");
    const tree = await screen.findByRole("tree", { name: "Live" });
    fireEvent.contextMenu(await screen.findByText("Video only"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start stream" }));
    expect(document.activeElement).toBe(tree);
  });
});

const MOUSE = { pointerType: "mouse" };

describe("a hover picture", () => {
  it("is asked for only on a camera the scan signed in to", async () => {
    await renderView();
    await screen.findByText("10.0.0.3");
    // A row with a picture has no text tip, which would show beside its card; one without keeps it.
    const tipped = (name: RegExp) => screen.getByRole("treeitem", { name }).hasAttribute("title");
    expect([tipped(/10\.0\.0\.2/), tipped(/10\.0\.0\.3/)]).toEqual([false, true]);
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /10\.0\.0\.3/ }), MOUSE);
    expect(fetched).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/live/snapshot"),
      undefined,
    );
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /10\.0\.0\.2/ }), MOUSE);
    await waitFor(() =>
      expect(fetched).toHaveBeenCalledWith("/api/live/snapshot/network/10.0.0.2", undefined),
    );
  });
});

describe("a long scan", () => {
  it("draws only some of the cameras it found", async () => {
    SNAPSHOT = {
      ...FOUND,
      rows: Array.from({ length: 500 }, (_, at) =>
        found(`10.1.${Math.floor(at / 250)}.${at % 250}`, { state: "Refused" }),
      ),
    };
    await renderView();
    await screen.findByText("10.1.0.0");
    const drawn = screen.getAllByRole("treeitem").length;
    expect(drawn).toBeGreaterThan(0);
    expect(drawn).toBeLessThan(100);
  });
});
