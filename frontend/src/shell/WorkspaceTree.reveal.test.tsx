import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { scenario } from "../session/fakeScenario";
import { FakeSocket, type Room } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionProvider } from "../session/session";
import { KeptStateProvider } from "../kit/keptState";
import { RevealShown } from "./RevealShown";
import { labels, LISTING, renderTree } from "./WorkspaceTree.fixture";
import { WorkspaceTree } from "./WorkspaceTree";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const REVEAL = scenario("reveal");
/** The rows of the playlist no viewer shows. */
const OTHER = ["other", "X", "GT"];
const { ids } = REVEAL;

const front = (room: Room, item: "mot" | "v") =>
  act(() =>
    room.publish({
      sidebar: room.presenter.sidebar,
      ...viewing(item === "mot" ? [ids.mot, { entry: ids["mot/A"] }] : ids.v),
    }),
  );

describe("the item a viewer shows", () => {
  it("is revealed with its section, which folds back when the viewer moves on", async () => {
    const room = await renderTree(REVEAL, "open");
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(labels()).toEqual(["v.mp4"]);

    front(room, "mot");
    expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "GT", ...OTHER]);

    front(room, "v");
    expect(labels()).toEqual(["v.mp4"]);
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    expect(labels()).toEqual(["v.mp4", "mot", ...OTHER]);
  });

  it("keeps the fold the user gave it while the viewer stays", async () => {
    const room = await renderTree(REVEAL, "open");
    front(room, "mot");
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    act(() => room.show(REVEAL, "open"));
    expect(labels()).toEqual(["v.mp4", "mot", ...OTHER]);
    fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
    front(room, "mot");
    expect(screen.queryByRole("tree", { name: "Playlists" })).toBeNull();
  });

  it("follows the viewer while its view is away", async () => {
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
    act(() => room.show(REVEAL, "open"));
    front(room, "mot");
    fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
    rerender(page(true));
    front(room, "v");
    front(room, "mot");
    rerender(page(false));
    expect(labels().slice(1, 4)).toEqual(["mot", "A", "GT"]);
  });

  it("shows the lanes of the entry an Enter opens, and keeps the fold the user then gives it", async () => {
    render(
      <SessionProvider>
        <KeptStateProvider>
          <RevealShown view="workspace" />
          <WorkspaceTree view="workspace" {...LISTING} />
        </KeptStateProvider>
      </SessionProvider>,
    );
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1") as Room;
    act(() => room.show(REVEAL));
    const tree = screen.getByRole("tree", { name: "Playlists" });
    expect(labels()).toEqual(["v.mp4", "mot", "A", "B", "other", "X"]);
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Enter" });
    await act(async () => {});
    expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "other", "X"]);
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(labels()).toEqual(["v.mp4", "mot", "A", "B", "other", "X"]);
  });

  describe("with several viewers", () => {
    const setup = async () => {
      render(
        <SessionProvider>
          <KeptStateProvider>
            <RevealShown view="workspace" />
            <WorkspaceTree view="workspace" {...LISTING} />
          </KeptStateProvider>
        </SessionProvider>,
      );
      await act(async () => {});
      const room = FakeSocket.rooms.get("room-1") as Room;
      act(() => room.show(REVEAL, "open"));
      return (active: number, ...shown: Parameters<typeof viewing>) =>
        act(() => room.publish({ sidebar: room.presenter.sidebar, ...viewing(...shown), active }));
    };
    const of = (...entries: string[]) => entries.map((entry) => [ids.mot, { entry }] as const);

    it("leaves a playlist the user closed closed when another entry is shown", async () => {
      const show = await setup();
      show(1, ...of(ids["mot/A"]));
      expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "other", "X"]);
      fireEvent.keyDown(screen.getByRole("tree", { name: "Playlists" }), { key: "ArrowLeft" });
      show(1, ...of(ids["mot/B"]));
      expect(labels()).toEqual(["v.mp4", "mot", "other", "X"]);
    });

    it("keeps every entry a viewer shows open, and closes one when its last viewer leaves it", async () => {
      const show = await setup();
      show(1, ...of(ids["mot/A"], ids["mot/B"]));
      expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "GT", "other", "X"]);
      show(2, ...of(ids["mot/B"], ids["mot/A"]));
      expect(labels()).toEqual(["v.mp4", "mot", "A", "GT", "DPM", "B", "GT", "other", "X"]);
      show(1, ...of(ids["mot/B"]));
      expect(labels()).toEqual(["v.mp4", "mot", "A", "B", "GT", "other", "X"]);
    });

    it("keeps every item a viewer shows open when the focus moves, and the section a playlist's viewer reveals", async () => {
      const show = await setup();
      const both = [
        [ids.mot, { entry: ids["mot/A"] }],
        [ids.other, { entry: ids["other/X"] }],
      ] as const;
      const all = ["mot", "A", "GT", "DPM", "B", "other", "X", "GT"];
      show(1, ...both);
      expect(labels()).toEqual(["v.mp4", ...all]);
      show(2, ...both);
      expect(labels()).toEqual(["v.mp4", ...all]);

      // Folding Playlists and showing a playlist beside a video reveals it for as long as that viewer stays.
      show(1, ids.v);
      fireEvent.click(screen.getByRole("button", { name: "Playlists" }));
      expect(labels()).toEqual(["v.mp4"]);
      show(1, [ids.mot, { entry: ids["mot/A"] }]);
      expect(labels().slice(1, 4)).toEqual(["mot", "A", "GT"]);
      show(2, [ids.mot, { entry: ids["mot/A"] }], ids.v);
      expect(labels().slice(1, 4)).toEqual(["mot", "A", "GT"]);
    });
  });
});
