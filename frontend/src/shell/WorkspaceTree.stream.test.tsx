import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import type { ItemRecipe, ItemState } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { CLIP, commands, renderTree, shown } from "./WorkspaceTree.fixture";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const recipe: ItemRecipe = { type: "Video", path: "/v.mp4", label: null, overlays: [] };
const stored = (item: ItemState): ItemState => ({ ...item, recipe });
const menuOf = (name: RegExp) => {
  fireEvent.contextMenu(screen.getByRole("treeitem", { name }));
  const names = screen.getAllByRole("menuitem").map((entry) => entry.textContent);
  fireEvent.keyDown(document, { key: "Escape" });
  return names;
};

const camera = (id: string, label: string): ItemState => ({
  id,
  label,
  kind: "live",
  open: false,
  resolution: {
    status: "Resolved",
    content: {
      kind: "LiveVideo",
      id,
      generation: 1,
      display_name: label,
      slot: "live",
      overlay: "none",
      lanes: [label],
      recipe: null,
    },
  },
});

it("starts a live stream beside the open viewers from its row and stops it by closing its viewer", async () => {
  const lobby = camera("cam", "Lobby");
  await renderTree([stored(CLIP), stored(lobby)], []);
  const live = () => screen.getByRole("treeitem", { name: /^Lobby/ });
  expect(menuOf(/^Lobby/)).toEqual(["Start stream", "Information", "Edit", "Remove"]);

  fireEvent.click(screen.getByRole("button", { name: "Start Lobby" }));
  await act(async () => {});
  expect(commands()).toEqual([{ type: "openViewer", item: "cam" }]);
  expect(shown().viewers).toMatchObject({ 1: { item: "cam" } });
  expect(live().getAttribute("aria-current")).toBe("true");
  expect(menuOf(/^Lobby/)[0]).toBe("Stop stream");

  fireEvent.click(screen.getByRole("button", { name: "Stop Lobby" }));
  await act(async () => {});
  expect(commands().at(-1)).toEqual({ type: "closeViewer", item: "cam" });
});

it("leaves the keys pressed on a row's own button to that button, not to the row the keyboard is on", async () => {
  await renderTree([stored(camera("a", "Lobby")), stored(camera("b", "Dock"))], []);
  fireEvent.click(screen.getByRole("treeitem", { name: /^Lobby/ }));
  const dock = screen.getByRole("button", { name: "Start Dock" });
  dock.focus();
  // Enter on the button is its own activation: the tree neither takes it nor starts the selected row.
  expect(fireEvent.keyDown(dock, { key: "Enter" })).toBe(true);
  expect(commands()).toEqual([]);
  fireEvent.click(dock);
  await act(async () => {});
  expect(commands()).toEqual([{ type: "openViewer", item: "b" }]);
});
