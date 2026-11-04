import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { FakeSettings } from "../fakeSettings";
import type { ItemState, NetworkRow, NetworkSnapshot, NewLiveWire } from "../generated/api";
import type { AddSeed } from "../live/cameras";
import { FakeSocket, type Room } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
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
  rows: [
    found("10.0.0.2", { state: "SignedIn", login_index: 0, architecture: "", soc: "" }),
    found("10.0.0.3", { state: "Refused" }),
  ],
};

const confirming: ItemState = {
  id: "a",
  label: "Lobby",
  kind: "live",
  open: false,
  resolution: {
    status: "Failed",
    code: "NeedsConfirmation",
    params: { item: "a", confirm: [{ reference: "CAM_PASS", host: "10.0.0.2" }] },
  },
};
const RECIPE: NewLiveWire = {
  host: "10.0.0.2",
  username: "root",
  protocol: "https",
  head: 1,
  rtsp_port: 554,
  overlay: { mode: "none" },
};
const ready = (id: string, recipe: NewLiveWire | null = null): ItemState => ({
  id,
  label: "Lobby",
  kind: "live",
  open: false,
  resolution: {
    status: "Resolved",
    content: {
      kind: "LiveVideo",
      id,
      generation: 1,
      display_name: "Lobby",
      slot: "live",
      overlay: "mqtt",
      lanes: ["Lobby"],
      recipe,
    },
  },
});

const fetched = vi.fn();
let resolvedFirst = true;
let refuseScan = false;
let loginRefused = false;

beforeEach(() => {
  SNAPSHOT = FOUND;
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      fetched(url, init?.body);
      // Whether the item was resolved already when the confirmation arrived.
      if (url === "/api/live/confirm")
        resolvedFirst = sent().some(({ type }) => type === "resolve");
      if (url === "/api/network/scan" && refuseScan) return new Response("{}", { status: 422 });
      if (url === "/api/live/check" && loginRefused)
        return new Response(JSON.stringify(found("10.0.0.2", { state: "Refused" })));
      if (url !== "/api/network") return new Response(null, { status: 204 });
      return new Response(`${JSON.stringify(SNAPSHOT)}\n`);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetched.mockClear();
  refuseScan = false;
  loginRefused = false;
  window.sessionStorage.clear();
  FakeSocket.reset();
});

async function renderView(items: ItemState[], onAdd: (seed: AddSeed) => void = () => {}) {
  render(
    <GrantsContext value={["files", "preferences"]}>
      <FakeSettings>
        <SessionProvider>
          <LiveView onAdd={onAdd} />
        </SessionProvider>
      </FakeSettings>
    </GrantsContext>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  act(() => room.setItems(items));
  return room;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );

describe("found cameras", () => {
  it("offers Add only after the scan signed in, and adds and starts the stream by address, with no dialog", async () => {
    const added = vi.fn();
    await renderView([], added);
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
    await renderView([]);
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
    await renderView([]);
    fireEvent.click(await screen.findByRole("button", { name: "Rescan" }));
    expect(await screen.findByText("Invalid IP range")).toBeTruthy();
  });
});

describe("workspace streams", () => {
  it("adds a stream of a new camera from the Live caption", async () => {
    const added = vi.fn();
    await renderView([], added);
    fireEvent.click(await screen.findByRole("button", { name: "Add stream" }));
    expect(added).toHaveBeenCalledWith({});
  });

  it("confirms the reference for its host, then resolves the item again", async () => {
    await renderView([confirming]);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm $CAM_PASS for 10.0.0.2" }));
    await waitFor(() => expect(sent()).toContainEqual({ type: "resolve", item: "a" }));
    expect(fetched).toHaveBeenCalledWith(
      "/api/live/confirm",
      JSON.stringify({ reference: "CAM_PASS", host: "10.0.0.2" }),
    );
    expect(resolvedFirst).toBe(false);
  });

  it("starts and stops a stream by opening and closing its viewer", async () => {
    await renderView([ready("a")]);
    fireEvent.click(await screen.findByRole("button", { name: "Start stream" }));
    await waitFor(() => expect(sent()).toContainEqual({ type: "openViewer", item: "a" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop stream" }));
    await waitFor(() => expect(sent()).toContainEqual({ type: "closeViewer", item: "a" }));
  });
});

describe("stream menu", () => {
  it("offers Information and Remove beside Start, and removes the item", async () => {
    await renderView([ready("a", RECIPE)]);
    fireEvent.contextMenu(await screen.findByText("Video only"));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Start stream",
      "Information",
      "Remove",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Information" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("device API protocol");
    expect(dialog.textContent).toContain("HTTPS");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.contextMenu(await screen.findByText("Video only"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    await waitFor(() => expect(sent()).toContainEqual({ type: "remove", item: "a" }));
  });

  it("takes Start from a stream whose camera refuses the login saved with it", async () => {
    loginRefused = true;
    await renderView([ready("a", RECIPE)]);
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
    await renderView([ready("a", RECIPE)]);
    const tree = await focusTree("Live");
    expect(activeLabel(tree)).toContain("10.0.0.2");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(activeLabel(tree)).toContain("Video only");
    fireEvent.keyDown(tree, { key: "Enter" });
    await waitFor(() => expect(sent()).toContainEqual({ type: "openViewer", item: "a" }));
    fireEvent.keyDown(tree, { key: "Delete" });
    await waitFor(() => expect(sent()).toContainEqual({ type: "remove", item: "a" }));
  });

  it("leaves Enter on a button inside a row to the button", async () => {
    await renderView([confirming]);
    const tree = await focusTree("Live");
    fireEvent.keyDown(
      await screen.findByRole("button", { name: "Confirm $CAM_PASS for 10.0.0.2" }),
      { key: "Enter" },
    );
    expect(sent()).toEqual([]);
    expect(tree.getAttribute("aria-activedescendant")).not.toBeNull();
  });

  it("gives the focus back to the tree when a row's menu closes", async () => {
    await renderView([ready("a", RECIPE)]);
    const tree = await screen.findByRole("tree", { name: "Live" });
    fireEvent.contextMenu(await screen.findByText("Video only"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start stream" }));
    expect(document.activeElement).toBe(tree);
  });
});

const MOUSE = { pointerType: "mouse" };

describe("a hover picture", () => {
  it("is asked for only on a camera the scan signed in to", async () => {
    await renderView([]);
    await screen.findByText("10.0.0.3");
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
    await renderView([]);
    await screen.findByText("10.1.0.0");
    const drawn = screen.getAllByRole("treeitem").length;
    expect(drawn).toBeGreaterThan(0);
    expect(drawn).toBeLessThan(100);
  });
});
