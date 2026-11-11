import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket } from "../session/fakeSession";
import { scenario } from "../session/fakeScenario";
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
const s = scenario("one-video");

// A reload replaces the viewer in one commit with the viewer of the Content's next generation, which plays on if the
// first was playing. Whatever else makes a viewer go away leaves the clock paused for the Guests.
describe("a viewer replaced by a reload", () => {
  /** The viewer of the item, playing or not; `show` moves the Doc to one of the states of its scenario. */
  async function open(playing: boolean) {
    const store = new SessionStore();
    render(
      <SessionProvider store={store}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1")!;
    act(() => room.show(s));
    await slider();
    vi.useFakeTimers();
    if (playing) {
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      flushFrames();
    }
    // What the window shows of the viewer's clock, which is what it tells the backend next.
    const published = () => store.readView().viewers[1]?.clock;
    const show = (state: string) => act(() => room.show(s, state));
    return { show, published, outgoing: sockets.length - 1 };
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
      const { show, published, outgoing } = await open(true);
      const revoke = () => act(() => sockets[outgoing]({ type: "closed", code: 4403 }));
      if (when === "before") revoke();
      show("reloaded");
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
    const { show, published } = await open(false);
    fireEvent.change(screen.getByRole("slider", { name: "Timeline" }), { target: { value: "5" } });
    show("reloaded");
    await showFrame(5);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(published()).toMatchObject({ frame: 5, playing: false });
  });

  it("leaves the clock paused when the new generation cannot open, and says why", async () => {
    const { show, published } = await open(true);
    show("reloaded");
    await act(() => vi.advanceTimersByTimeAsync(50));
    answer({ type: "Error", code: "NotAVideo", params: {}, reason: "not a video" });
    expect(screen.getByRole("alert").textContent).toBe("not a video");
    expect(published()).toMatchObject({ playing: false });
  });

  it("leaves the clock paused when the item goes away, and opens paused when it resolves again later", async () => {
    const { show, published } = await open(true);
    show("removed");
    expect(published()).toMatchObject({ playing: false });

    // The item came back as new Content some time after, not in the commit that replaced the viewer.
    await act(() => vi.advanceTimersByTimeAsync(10));
    show("reloaded");
    await showFrame(published()?.frame ?? 0);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(published()).toMatchObject({ playing: false });
  });

  it("follows the presenter after a reload, and publishes nothing", async () => {
    const sent: ClientMessage[] = [];
    const room = FakeSocket.host("watch");
    room.show(s);
    room.presenter = {
      ...room.presenter,
      ...viewing([s.ids.a, { clock: { frame: 12, playing: false, speedCenti: 100, atUs: 0 } }]),
    };
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    const store = new SessionStore({ now: () => 10_000 });
    render(
      <SessionProvider store={store}>
        <Viewer sent={sent} />
      </SessionProvider>,
    );
    expect((await slider()).value).toBe("12");
    act(() => room.show(s, "reloaded"));
    await act(async () => {});
    expect((await slider()).value).toBe("12");
    flushFrames();
    expect(viewsSent()).toHaveLength(0);
  });
});
