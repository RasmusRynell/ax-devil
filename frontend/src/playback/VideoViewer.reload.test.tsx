import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket, video } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import { videoFrame } from "./fakePlayback";
import type { ClientMessage } from "./protocol";
import {
  Viewer,
  answer,
  flushFrames,
  installViewerHarness,
  slider,
  sockets,
  viewsSent,
} from "./videoViewerHarness";

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

installViewerHarness();

// A reload replaces the viewer in one commit with the viewer of the Content's next generation, which plays on if the
// first was playing. Whatever else makes a viewer go away leaves the clock paused for the watchers.
describe("a viewer replaced by a reload", () => {
  /** The viewer of generation 1 of the item, playing or not; `tree(null)` takes it away. */
  async function open(playing: boolean) {
    const sent: ClientMessage[] = [];
    const store = new SessionStore();
    const tree = (generation: number | null) => (
      <SessionProvider store={store}>
        {generation === null ? null : <Viewer sent={sent} generation={generation} />}
      </SessionProvider>
    );
    const view = render(tree(1));
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1")!;
    act(() => room.setItems([video("a")]));
    await slider();
    vi.useFakeTimers();
    if (playing) {
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      flushFrames();
    }
    // What the window shows of the viewer's clock, which is what it tells the backend next.
    const published = () => store.readView().viewers[1]?.clock;
    return { view, tree, published, outgoing: sockets.length - 1 };
  }
  /** The backend sends frame `id` on the newest playback socket, and the display refreshes. */
  const showFrame = async (id: number) => {
    await act(() => vi.advanceTimersByTimeAsync(50)); // the new stream asks for its ticket and opens
    act(() =>
      sockets.at(-1)?.({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(id) } }),
    );
    await act(() => vi.advanceTimersByTimeAsync(200));
    flushFrames();
  };
  const frameOnScreen = () =>
    Number((screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement).value);

  it.each(["before", "after"])(
    "plays on from the old playhead when the old stream is revoked %s the new generation arrives",
    async (when) => {
      const { view, tree, published, outgoing } = await open(true);
      const revoke = () => act(() => sockets[outgoing]({ type: "closed", code: 4403 }));
      if (when === "before") revoke();
      view.rerender(tree(2));
      if (when === "after") revoke();
      const left = published();
      expect(left).toMatchObject({ playing: false });
      expect(left?.frame).toBeGreaterThan(0);

      await showFrame(left?.frame ?? 0);
      expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
      expect(published()).toMatchObject({ playing: true });
      expect(frameOnScreen()).toBeGreaterThanOrEqual(left?.frame ?? Infinity);
    },
  );

  it("stays paused, and publishes it, when it was paused", async () => {
    const { view, tree, published } = await open(false);
    fireEvent.change(screen.getByRole("slider", { name: "Timeline" }), { target: { value: "5" } });
    view.rerender(tree(2));
    await showFrame(5);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(published()).toMatchObject({ frame: 5, playing: false });
  });

  it("leaves the clock paused when the new generation cannot open, and says why", async () => {
    const { view, tree, published } = await open(true);
    view.rerender(tree(2));
    await act(() => vi.advanceTimersByTimeAsync(50));
    answer({ type: "Error", code: "NotAVideo", params: {}, reason: "not a video" });
    expect(screen.getByRole("alert").textContent).toBe("not a video");
    expect(published()).toMatchObject({ playing: false });
  });

  it("leaves the clock paused when the item goes away, and opens paused when it resolves again later", async () => {
    const { view, tree, published } = await open(true);
    view.rerender(tree(null));
    expect(published()).toMatchObject({ playing: false });

    // The item came back as new Content some time after, not in the commit that replaced the viewer.
    await act(() => vi.advanceTimersByTimeAsync(10));
    view.rerender(tree(2));
    await showFrame(published()?.frame ?? 0);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(published()).toMatchObject({ playing: false });
  });

  it("follows the presenter after a reload, and publishes nothing", async () => {
    const sent: ClientMessage[] = [];
    const room = FakeSocket.host("watch");
    room.setItems([video("a")]);
    room.presenter = {
      ...room.presenter,
      ...viewing(["a", { clock: { frame: 12, playing: false, speedCenti: 100, atUs: 0 } }]),
    };
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    const store = new SessionStore({ now: () => 10_000 });
    const tree = (generation: number) => (
      <SessionProvider store={store}>
        <Viewer sent={sent} generation={generation} />
      </SessionProvider>
    );
    const view = render(tree(1));
    expect((await slider()).value).toBe("12");
    view.rerender(tree(2));
    await act(async () => {});
    expect((await slider()).value).toBe("12");
    flushFrames();
    expect(viewsSent()).toHaveLength(0);
  });
});
