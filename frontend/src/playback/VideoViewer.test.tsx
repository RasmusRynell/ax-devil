import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket } from "../session/fakeSession";
import { scenario } from "../session/fakeScenario";
import { viewing } from "../session/fakeViews";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import { exportOf } from "../viewer/exportTargets";
import { NOTHING_HIDDEN, type Hidden } from "../viewer/hidden";
import { videoFrame } from "./fakePlayback";
import type { ClientMessage, LaneInfo, ServerMessage } from "./protocol";
import type { VideoViewerProps } from "./VideoViewer";
import {
  Viewer,
  answer,
  flushFrames,
  installViewerHarness,
  slider,
  setSpeed,
  sockets,
  speed,
  viewsSent,
} from "./videoViewerHarness";

// The picture needs WebGL; this suite is about the session behind it. The canvas stands in with a one-lane geometry,
// and shows the zoom it is given; a click on it zooms to 2.
vi.mock("./VideoCanvas", async () => {
  const { FIT } = await import("./viewport");
  const { useEffect } = await import("react");
  const cell = { x: 0, y: 0, width: 160, height: 90 };
  const geometry = { reference: () => ({ cell, image: cell }), boxes: () => [] };
  return {
    VideoCanvas: ({
      viewport,
      onViewport,
      onDrawn,
    }: {
      viewport: { zoom: number };
      onViewport?: (next: { zoom: number; panX: number; panY: number }) => void;
      onDrawn?: (geometry: unknown) => void;
    }) => {
      useEffect(() => onDrawn?.(geometry));
      return (
        <div
          data-testid="canvas"
          data-zoom={viewport.zoom}
          onClick={() => onViewport?.({ zoom: 2, panX: 0, panY: 0 })}
        />
      );
    },
  };
});

installViewerHarness();
const s = scenario("one-video");

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
    act(() => room.show(s));
    fireEvent.change(await slider(), { target: { value: "37" } });
    setSpeed("2.5");
    expect(sent[0]).toMatchObject({ type: "Open", ticket: expect.any(String) });
    view.rerender(<SessionProvider>{null}</SessionProvider>);
    flushFrames();
    expect(room.presenter.viewers[1]?.clock).toMatchObject({
      frame: 37,
      playing: false,
      speedCenti: 250,
    });
    // The backend reads the stamp as a whole number of microseconds and closes the connection on a fraction.
    expect(Number.isInteger(room.presenter.viewers[1]?.clock?.atUs)).toBe(true);

    sent.length = 0;
    view.rerender(
      <SessionProvider>
        <Viewer sent={sent} />
      </SessionProvider>,
    );
    expect((await slider()).value).toBe("37");
    expect(speed().textContent).toBe("2.5×");
    expect(sent[0]).toMatchObject({ type: "Open", start_frame: 37 });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("takes the keyboard when it opens, so the shell's shortcuts reach it at once", async () => {
    render(
      <SessionProvider>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    await act(async () => {});
    act(() => FakeSocket.rooms.get("room-1")!.show(s));
    expect(document.activeElement).toBe((await slider()).closest("[tabindex='-1']"));
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
    const clock = { frame: 10, playing: true, speedCenti: 100, atUs: serverNow - 400_000 };
    room.presenter = { ...room.presenter, ...viewing([s.ids.a, { clock }]) };
    act(() => room.show(s));
    expect((await slider()).value).toBe("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    // The Guests' clock is right as it stands: nothing to correct.
    flushFrames();
    expect(viewsSent()).toHaveLength(0);
    expect(room.presenter.viewers[1]?.clock).toMatchObject({ playing: true });
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
      act(() => FakeSocket.rooms.get("room-1")!.show(s));
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

    it("opens the new stream where playback had got to, the frame the Guests were told", async () => {
      const sent: ClientMessage[] = [];
      render(
        <SessionProvider>
          <Viewer sent={sent} />
        </SessionProvider>,
      );
      await act(async () => {});
      const room = FakeSocket.rooms.get("room-1")!;
      act(() => room.show(s));
      await slider();
      vi.useFakeTimers();
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      flushFrames();
      act(() => FakeSocket.latest.drop());
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      const told = room.presenter.viewers[1]?.clock;
      const thumb = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
      expect(told).toMatchObject({ playing: false });
      expect(told?.frame).toBeGreaterThan(0);
      expect(opens(sent)).toHaveLength(2);
      expect(opens(sent)[1]).toMatchObject({ start_frame: told?.frame });
      expect(thumb.value).toBe(String(told?.frame));
    });

    it("still plays an ordinary open that restarted before its first frame, unless the viewer acted", async () => {
      // The resume helper runs on fake timers, which also fake the display refresh.
      const firstFrame = async () => {
        act(() =>
          sockets.at(-1)?.({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(0) } }),
        );
        await act(() => vi.advanceTimersByTimeAsync(20));
      };
      await resume();
      await firstFrame();
      expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();

      cleanup();
      FakeSocket.reset();
      vi.useRealTimers();
      render(
        <SessionProvider>
          <Viewer sent={[]} />
        </SessionProvider>,
      );
      await act(async () => {});
      act(() => FakeSocket.rooms.get("room-1")!.show(s));
      fireEvent.change(await slider(), { target: { value: "5" } });
      vi.useFakeTimers();
      act(() => FakeSocket.latest.drop());
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      await firstFrame();
      expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    });

    it("stays failed, asking once, when the backend no longer lets the seat play it", async () => {
      const sent = await resume(true);
      expect(opens(sent)).toHaveLength(1);
      expect(ticketRequests()).toHaveLength(2);
      expect(screen.queryByRole("slider", { name: "Timeline" })).toBeNull();
    });
  });

  /**
   * A window that joined a session by link, or attached to it as an Owner seat, whose presenter plays frame 10 of "a"
   * (400 ms in) 400 ms of backend time ago.
   */
  function follower(access: "watch" | "explore" | "attach") {
    const room = FakeSocket.host(access === "attach" ? "watch" : access);
    room.show(s);
    FakeSocket.clockOffsetUs = 5_000_000;
    const serverNow = 10_000 * 1000 + FakeSocket.clockOffsetUs;
    const clock = { frame: 10, playing: true, speedCenti: 100, atUs: serverNow - 400_000 };
    room.presenter = { ...room.presenter, ...viewing([s.ids.a, { clock }]) };
    if (access !== "attach")
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
    if (access === "attach") act(() => void store.attach(room.id));
    return room;
  }

  /** The stream can open before the backend's clock has answered; the follower then moves on to the right frame. */
  const settled = async (frame: string) => {
    const thumb = await slider();
    await waitFor(() => expect(thumb.value).toBe(frame));
  };

  it("follows the presenter's clock by the backend's time, and a Guest has no controls of its own", async () => {
    follower("watch");
    await settled("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect((await slider()).disabled).toBe(true);
  });

  it("shows an Owner seat the clock another Owner seat set, and publishes its own seek for that seat", async () => {
    const room = follower("attach");
    await settled("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect((await slider()).disabled).toBe(false);
    flushFrames();
    expect(viewsSent()).toHaveLength(0);

    fireEvent.change(await slider(), { target: { value: "70" } });
    flushFrames();
    expect(room.presenter.viewers[1]?.clock).toMatchObject({ frame: 70 });
  });

  it("takes another Owner seat's change even when it equals the window's own last one", async () => {
    const room = follower("attach");
    await settled("20");
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.change(await slider(), { target: { value: "70" } });
    flushFrames();
    const own = room.presenter.viewers[1]!.clock!;
    expect(room.presenterBy).toBe(2);

    // The other seat (1) seeks elsewhere, then back to what this window had set: it must follow both.
    const clockOf = (clock: typeof own) => ({
      ...room.presenter,
      ...viewing([s.ids.a, { clock }]),
    });
    act(() => room.publish(clockOf({ ...own, frame: 40, playing: false, atUs: own.atUs + 1 }), 1));
    await settled("40");
    act(() => room.publish(clockOf({ ...own, atUs: own.atUs + 2 }), 1));
    await settled(String(own.frame));
  });

  it("shows an Owner seat the viewport another Owner seat set and sends its own, whose echo it ignores", async () => {
    const room = follower("attach");
    await settled("20");
    const zoom = () => screen.getByTestId("canvas").dataset.zoom;
    const region = (w: number) => ({
      ...room.presenter,
      viewers: { 1: { ...room.presenter.viewers[1]!, viewport: { x: 0, y: 0, w, h: w } } },
    });
    expect(zoom()).toBe("1");

    act(() => room.publish(region(0.25), 1));
    await waitFor(() => expect(zoom()).toBe("4"));

    fireEvent.click(screen.getByTestId("canvas"));
    expect(zoom()).toBe("2");
    await waitFor(() => expect(room.presenter.viewers[1]?.viewport).toMatchObject({ w: 0.5 }));
    expect(room.presenterBy).toBe(2);
    // Its own change coming back is not applied over the window.
    expect(zoom()).toBe("2");

    // Another seat's newer region that arrives while its own change is unanswered is taken once it is answered.
    await act(() => new Promise((done) => setTimeout(done, 120)));
    fireEvent.click(screen.getByTestId("canvas"));
    act(() => room.publish(region(0.25), 1));
    await waitFor(() => expect(zoom()).toBe("4"));
  });

  it("publishes a seek made while following, so the seat explores, and never the presenter's own clock", async () => {
    const room = follower("explore");
    await settled("20");
    flushFrames();
    // The presenter's clock drives this window; none of it goes back.
    expect(viewsSent()).toHaveLength(0);

    fireEvent.change(await slider(), { target: { value: "70" } });
    flushFrames();
    expect(viewsSent()).toHaveLength(1);
    expect(room.seats[1]?.view?.viewers[1]?.clock).toMatchObject({ frame: 70 });
  });

  describe("lanes", () => {
    const LANES: LaneInfo[] = [
      { label: "DPM", slot: "DPM", source: 0 },
      { label: "GT", slot: "GT", source: 1 },
    ];
    const mountLanes = async (
      onHideLane?: (label: string) => void,
      onLaneMatching?: VideoViewerProps["onLaneMatching"],
    ) => {
      const sent: ClientMessage[] = [];
      render(
        <SessionProvider>
          <Viewer
            sent={sent}
            lanes={LANES}
            onHideLane={onHideLane}
            onLaneMatching={onLaneMatching}
          />
        </SessionProvider>,
      );
      await act(async () => {});
      act(() => FakeSocket.rooms.get("room-1")!.show(s));
      await slider();
      return sent;
    };
    const ready: ServerMessage = {
      type: "LaneStatus",
      lane: 1,
      status: {
        status: "Ready",
        bases: ["Frame"],
        default_basis: "Frame",
        filter: [{ id: "pedestrian", label: "Pedestrian", default_enabled: true }],
        warnings: [
          {
            problem: "Skipped 2 rows",
            reasons: ["row 4"],
            consequence: "They are not drawn",
            more_reasons: true,
          },
        ],
        samples: 4,
        data_revision: 1,
        alignment: null,
        mean_sample_bytes: 100,
        ingest: { state: "Complete" },
      },
    };

    it("sends the lane menu's changes the backend selects by as a new policy, and keeps the rest to itself", async () => {
      const onLaneMatching = vi.fn();
      const sent = await mountLanes(undefined, onLaneMatching);
      answer(ready);
      fireEvent.click(screen.getByRole("button", { name: "GT: settings" }));
      const before = sent.length;
      fireEvent.click(screen.getByRole("tab", { name: "Filter" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Pedestrian" }));
      expect(sent).toHaveLength(before);
      fireEvent.click(screen.getByRole("tab", { name: "Timing" }));
      // The lane started from the saved default, which is on.
      fireEvent.click(screen.getByRole("switch", { name: "Sticky" }));
      expect(sent.at(-1)).toEqual({
        type: "LanePolicy",
        lane: 1,
        revision: 2,
        policy: { basis: null, tolerance_ms: 50, sticky: { enabled: false, timeout_ms: 2050 } },
        subscribed: true,
      });
      expect(onLaneMatching).toHaveBeenCalledTimes(1);
      expect(onLaneMatching).toHaveBeenCalledWith(
        "GT",
        (sent.at(-1) as { policy: unknown }).policy,
      );
    });

    it("hides a lane through the callback it is given, and offers no hiding without one", async () => {
      const onHideLane = vi.fn();
      await mountLanes(onHideLane);
      fireEvent.click(screen.getByRole("button", { name: "DPM: settings" }));
      fireEvent.click(screen.getByRole("button", { name: "Hide lane" }));
      expect(onHideLane).toHaveBeenCalledWith("DPM");
      expect(screen.queryByRole("dialog")).toBeNull();

      cleanup();
      await mountLanes();
      fireEvent.click(screen.getByRole("button", { name: "DPM: settings" }));
      expect(screen.queryByRole("button", { name: "Hide lane" })).toBeNull();
    });

    it("has no transport while every lane is hidden, and has it again once one is shown", async () => {
      const sent: ClientMessage[] = [];
      const hide = (...labels: string[]): Hidden => ({
        entry: () => false,
        lane: (_, label) => labels.includes(label),
      });
      const view = (hidden: Hidden) => (
        <SessionProvider>
          <Viewer sent={sent} lanes={LANES} hidden={hidden} />
        </SessionProvider>
      );
      const mounted = render(view(NOTHING_HIDDEN));
      await act(async () => {});
      act(() => FakeSocket.rooms.get("room-1")!.show(s));
      await slider();
      mounted.rerender(view(hide("DPM", "GT")));
      expect(screen.getByRole("status").textContent).toBe("All lanes are hidden");
      expect(screen.queryByRole("slider", { name: "Timeline" })).toBeNull();
      mounted.rerender(view(hide("DPM")));
      expect(screen.getByRole("slider", { name: "Timeline" })).toBeTruthy();
    });

    it("tells the render catalogs are still loading instead of exporting, and keeps playing", async () => {
      await mountLanes();
      const play = screen.queryByRole("button", { name: "Play" });
      if (play) fireEvent.click(play);
      expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
      act(() => exportOf(s.ids.a)?.());
      expect(screen.getByRole("heading", { name: "Export video" })).toBeTruthy();
      expect(screen.getByText(/Render catalogs are still loading/)).toBeTruthy();
      expect(screen.queryByRole("heading", { name: "Export Video with Overlays" })).toBeNull();
      expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    });

    it("lists a lane's warnings on its tag", async () => {
      await mountLanes();
      answer(ready);
      fireEvent.click(screen.getByRole("button", { name: "GT: warnings" }));
      const panel = screen.getByRole("dialog", { name: "GT: warnings" });
      expect(panel.textContent).toContain("Skipped 2 rows");
      expect(panel.textContent).toContain("They are not drawn");
      expect(panel.textContent).toContain("row 4");
    });
  });
});
