import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useMemo } from "react";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider, useDoc } from "../session/session";
import { SessionStore } from "../session/store";
import { PLAYBACK_PROTOCOL, type ClientMessage } from "./protocol";
import { VideoViewer } from "./VideoViewer";
import type { Connect, Incoming } from "./wire";

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

let frames: FrameRequestCallback[] = [];
const flushFrames = () => act(() => frames.splice(0).forEach((frame) => frame(0)));

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  frames = [];
});

const content = {
  kind: "SeekableVideo" as const,
  id: "content-a",
  generation: 1,
  display_name: "a.mp4",
};

/** The video viewer on a fake playback socket that opens a 100-frame video as soon as it is asked. */
function Viewer({ sent }: { sent: ClientMessage[] }) {
  // One connect for the life of the viewer: a new one would restart playback by itself.
  const connect = useMemo<Connect>(() => open(sent), [sent]);
  // The viewer opens once the Doc has its item, as the shell does.
  return useDoc()?.workspace.items.length ? (
    <VideoViewer item="a" content={content} connect={connect} />
  ) : null;
}

function open(sent: ClientMessage[]): Connect {
  return (onIncoming) => {
    const say = (incoming: Incoming) => act(() => onIncoming(incoming));
    return {
      send: (message) => {
        sent.push(message);
        if (message.type !== "Open") return;
        say({
          type: "message",
          message: {
            type: "Opened",
            protocol: PLAYBACK_PROTOCOL,
            frame_count: 100,
            duration_us: 4_000_000,
            frame_bytes: 24,
          },
        });
        const times = Float64Array.from({ length: 100 }, (_, id) => id * 40_000);
        say({ type: "envelope", envelope: { kind: "timeline", times } });
      },
      close: () => {},
    };
  };
}

const slider = () => screen.findByRole("slider", { name: "Timeline" }) as Promise<HTMLInputElement>;
const speed = () => screen.getByRole("spinbutton", { name: "Speed" }) as HTMLInputElement;

describe("video viewer", () => {
  it("publishes where it was and at what speed when it goes away, and opens there when shown again", async () => {
    const sent: ClientMessage[] = [];
    const view = render(
      <SessionProvider>
        <Viewer sent={sent} />
      </SessionProvider>,
    );
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1")!;
    act(() => room.setItems([video("a")]));
    fireEvent.change(await slider(), { target: { value: "37" } });
    fireEvent.change(speed(), { target: { value: "2.5" } });
    expect(sent[0]).toMatchObject({ type: "Open", ticket: expect.any(String) });
    view.rerender(<SessionProvider>{null}</SessionProvider>);
    flushFrames();
    expect(room.presenter.viewers.a?.clock).toMatchObject({
      frame: 37,
      playing: false,
      speedCenti: 250,
    });
    // The backend reads the stamp as a whole number of microseconds and closes the connection on a fraction.
    expect(Number.isInteger(room.presenter.viewers.a?.clock?.atUs)).toBe(true);

    sent.length = 0;
    view.rerender(
      <SessionProvider>
        <Viewer sent={sent} />
      </SessionProvider>,
    );
    expect((await slider()).value).toBe("37");
    expect(speed().value).toBe("2.5");
    expect(sent[0]).toMatchObject({ type: "Open", start_frame: 37 });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("picks up a clock the accepted intent still has running when the owner reloads", async () => {
    // The page reloaded while playing frame 10 of "a" 400 ms (10 frames) of backend time ago.
    window.sessionStorage.removeItem("ax-devil.credential");
    const serverNow = 10_000 * 1000 + FakeSocket.clockOffsetUs;
    render(
      <SessionProvider store={new SessionStore({ now: () => 10_000 })}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1")!;
    room.presenter = {
      ...room.presenter,
      viewers: {
        a: { clock: { frame: 10, playing: true, speedCenti: 100, atUs: serverNow - 400_000 } },
      },
    };
    act(() => room.setItems([video("a")]));
    expect((await slider()).value).toBe("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    // The watchers' clock is right as it stands: nothing to correct.
    flushFrames();
    expect(viewsSent()).toHaveLength(0);
    expect(room.presenter.viewers.a?.clock).toMatchObject({ playing: true });
  });

  describe("when the session resumes", () => {
    /** Opens the viewer, then drops the session's socket and lets it reconnect, with tickets refused or not. */
    async function resume(refused = false) {
      const sent: ClientMessage[] = [];
      render(
        <SessionProvider>
          <Viewer sent={sent} />
        </SessionProvider>,
      );
      await act(async () => {});
      act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
      await slider();
      vi.useFakeTimers();
      FakeSocket.refuseTickets = refused;
      act(() => FakeSocket.latest.drop());
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      return sent;
    }
    const opens = (sent: ClientMessage[]) => sent.filter((message) => message.type === "Open");
    const ticketRequests = () =>
      FakeSocket.sockets
        .flatMap((socket) => socket.received)
        .filter((m) => m.type === "command" && m.cmd.type === "requestTicket");

    it("opens a fresh stream, because the streams of the earlier seating are ended", async () => {
      const sent = await resume();
      expect(opens(sent)).toHaveLength(2);
      expect(new Set(opens(sent).map((open) => open.ticket)).size).toBe(2);
      expect(screen.getByRole("slider", { name: "Timeline" })).toBeTruthy();
    });

    it("opens the new stream where playback had got to, the frame the watchers were told", async () => {
      const sent: ClientMessage[] = [];
      render(
        <SessionProvider>
          <Viewer sent={sent} />
        </SessionProvider>,
      );
      await act(async () => {});
      const room = FakeSocket.rooms.get("room-1")!;
      act(() => room.setItems([video("a")]));
      await slider();
      vi.useFakeTimers();
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      flushFrames();
      act(() => FakeSocket.latest.drop());
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      const told = room.presenter.viewers.a?.clock;
      const thumb = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
      expect(told).toMatchObject({ playing: false });
      expect(told?.frame).toBeGreaterThan(0);
      expect(opens(sent)).toHaveLength(2);
      expect(opens(sent)[1]).toMatchObject({ start_frame: told?.frame });
      expect(thumb.value).toBe(String(told?.frame));
    });

    it("stays failed, asking once, when the backend no longer lets the seat play it", async () => {
      const sent = await resume(true);
      expect(opens(sent)).toHaveLength(1);
      expect(ticketRequests()).toHaveLength(2);
      expect(screen.queryByRole("slider", { name: "Timeline" })).toBeNull();
    });
  });

  /** A window that joined a session whose presenter plays frame 10 of "a" (400 ms in) 400 ms of backend time ago. */
  function follower(access: "watch" | "explore") {
    const room = FakeSocket.host(access);
    room.setItems([video("a")]);
    FakeSocket.clockOffsetUs = 5_000_000;
    const serverNow = 10_000 * 1000 + FakeSocket.clockOffsetUs;
    room.presenter = {
      ...room.presenter,
      viewers: {
        a: { clock: { frame: 10, playing: true, speedCenti: 100, atUs: serverNow - 400_000 } },
      },
    };
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    // The backend's clock is known at once, because this window's own never moves.
    const store = new SessionStore({ now: () => 10_000 });
    render(
      <SessionProvider store={store}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    return room;
  }
  const viewsSent = () => FakeSocket.latest.received.filter((message) => message.type === "view");

  it("follows the presenter's clock by the backend's time, and a watcher has no controls of its own", async () => {
    follower("watch");
    expect((await slider()).value).toBe("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect((await slider()).disabled).toBe(true);
  });

  it("publishes a seek made while following, so the seat explores, and never the presenter's own clock", async () => {
    const room = follower("explore");
    expect((await slider()).value).toBe("20");
    flushFrames();
    // The presenter's clock drives this window; none of it goes back.
    expect(viewsSent()).toHaveLength(0);

    fireEvent.change(await slider(), { target: { value: "70" } });
    flushFrames();
    expect(viewsSent()).toHaveLength(1);
    expect(room.seats[1]?.view?.viewers.a?.clock).toMatchObject({ frame: 70 });
  });
});
