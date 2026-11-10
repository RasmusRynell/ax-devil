import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { labels, renderTree } from "./WorkspaceTree.fixture";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const search = (text: string) =>
  fireEvent.change(screen.getByRole("searchbox", { name: "Search items" }), {
    target: { value: text },
  });
const topLevel = () =>
  screen
    .queryAllByRole("treeitem")
    .filter((row) => row.getAttribute("aria-level") === "1")
    .map((row) => row.textContent);

describe("search", () => {
  it("reveals a match inside a collapsed row and a folded section, and clearing brings the folds back", async () => {
    await renderTree();
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot"]);
    search(" gt");
    expect(labels()).toEqual(["mot", "A", "GT"]);
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot"]);

    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(screen.queryByRole("tree", { name: "Playlists" })).toBeNull();
    search("GT");
    expect(labels()).toEqual(["mot", "A", "GT"]);
    // Folding during the search lasts until the text changes and leaves the saved fold alone.
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(screen.queryByRole("tree", { name: "Playlists" })).toBeNull();
    search("gt");
    expect(labels()).toEqual(["mot", "A", "GT"]);
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    search("");
    expect(screen.queryByRole("tree", { name: "Playlists" })).toBeNull();
  });

  it("says when nothing matches", async () => {
    await renderTree();
    search("absent");
    expect(screen.getByText("No matches")).toBeTruthy();
    expect(screen.queryByRole("tree")).toBeNull();
  });
});

describe("Sort", () => {
  it("keeps the Name order when the workspace changes", async () => {
    const places = scenario("tree-locations");
    const room = await renderTree(places);
    fireEvent.click(screen.getByRole("button", { name: "Sort" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Name" }));
    act(() => room.show(places, "added"));
    expect(topLevel()).toEqual([
      "a.mp4",
      "ab.mp4",
      "also-none.mp4, unavailable: File not found",
      "B.mp4",
      "c.mp4",
      "none.mp4, unavailable: File not found",
      "same.mp4, p",
      "same.mp4, q",
    ]);
  });
});
