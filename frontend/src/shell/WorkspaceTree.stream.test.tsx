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

it("starts a live stream beside the open viewers from its row and stops it by closing its viewer", async () => {
  const lobby: ItemState = {
    id: "cam",
    label: "Lobby",
    kind: "live",
    open: false,
    resolution: {
      status: "Resolved",
      content: {
        kind: "LiveVideo",
        id: "cam",
        generation: 1,
        display_name: "Lobby",
        slot: "live",
        overlay: "none",
        lanes: ["Lobby"],
        recipe: null,
      },
    },
  };
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
