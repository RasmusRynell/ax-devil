import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSocket, type Room } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { KeptStateProvider } from "./keptState";
import { RevealShown } from "./RevealShown";
import { CLIP, labels, LISTING, MOT, renderTree } from "./WorkspaceTree.fixture";
import { WorkspaceTree } from "./WorkspaceTree";

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

  it("follows the viewer while its view is away", async () => {
    const items = [
      { ...MOT, open: true },
      { ...CLIP, open: true },
    ];
    const page = (away: boolean) => (
      <SessionProvider>
        <KeptStateProvider>
          <RevealShown view="workspace" />
          {!away && <WorkspaceTree view="workspace" {...LISTING} />}
        </KeptStateProvider>
      </SessionProvider>
    );
    const { rerender } = render(page(false));
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1") as Room;
    act(() => room.setItems(items));
    front(room, "mot");
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    rerender(page(true));
    front(room, "v");
    front(room, "mot");
    rerender(page(false));
    expect(labels().slice(1, 4)).toEqual(["mot", "A", "GT"]);
  });
});
