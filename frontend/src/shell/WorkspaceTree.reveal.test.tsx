import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { FakeSocket, type Room } from "../session/fakeSession";
import { CLIP, labels, MOT, renderTree } from "./WorkspaceTree.fixture";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const front = (room: Room, active: string) =>
  act(() =>
    room.publish({
      sidebar: room.presenter.sidebar,
      active,
      viewers: { mot: { entry: "mot/A" }, v: {} },
    }),
  );

describe("the item a viewer shows", () => {
  it("is revealed with its section, which folds back when the viewer moves on", async () => {
    const room = await renderTree(
      [
        { ...MOT, open: true },
        { ...CLIP, open: true },
      ],
      [],
    );
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(labels()).toEqual(["v.mp4"]);

    front(room, "mot");
    expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "GT"]);

    front(room, "v");
    expect(labels()).toEqual(["v.mp4"]);
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(labels()).toEqual(["v.mp4", "mot"]);
  });

  it("keeps the fold the user gave it while the viewer stays", async () => {
    const room = await renderTree(
      [
        { ...MOT, open: true },
        { ...CLIP, open: true },
      ],
      [],
    );
    front(room, "mot");
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    act(() =>
      room.setItems([
        { ...MOT, open: true },
        { ...CLIP, open: true },
      ]),
    );
    expect(labels()).toEqual(["v.mp4", "mot"]);
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    front(room, "mot");
    expect(screen.queryByRole("tree", { name: "Playlists" })).toBeNull();
  });
});
