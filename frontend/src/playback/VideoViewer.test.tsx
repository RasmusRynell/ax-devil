import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket, video } from "../session/fakeSession";
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

// The picture needs WebGL; this suite is about the session behind it.
vi.mock("./VideoCanvas", () => ({ VideoCanvas: () => null }));

installViewerHarness();

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
    act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
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
    room.presenter = { ...room.presenter, ...viewing(["a", { clock }]) };
    act(() => room.setItems([video("a")]));
    expect((await slider()).value).toBe("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    // The watchers' clock is right as it stands: nothing to correct.
    flushFrames();
    expect(viewsSent()).toHaveLength(0);
    expect(room.presenter.viewers[1]?.clock).toMatchObject({ playing: true });
  });

  it("carries on playing from the playhead when a reload replaces the viewer, as the watchers were told", async () => {
    const tree = (generation: number) => (
      <SessionProvider>
        <Viewer sent={[]} generation={generation} />
      </SessionProvider>
    );
    const view = render(tree(1));
    await act(async () => {});
    const room = FakeSocket.rooms.get("room-1")!;
    act(() => room.setItems([video("a")]));
    await slider();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    flushFrames();
    // The backend revokes the outgoing stream before the new generation reaches the window: that is no stop.
    act(() => sockets.at(-1)?.({ type: "closed", code: 4403 }));
    view.rerender(tree(2));
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    flushFrames();
    // The outgoing viewer left its clock running at the playhead, and the replacement plays on from it.
    const told = room.presenter.viewers[1]?.clock;
    expect(told).toMatchObject({ playing: true });
    expect(told?.frame).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    const shown = Number(
      (screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement).value,
    );
    expect(shown).toBeGreaterThan(told?.frame ?? Infinity);
  });

  it("leaves no clock behind when its pane is closed while it plays", async () => {
    const store = new SessionStore();
    const view = render(
      <SessionProvider store={store}>
        <Viewer sent={[]} />
      </SessionProvider>,
    );
    await act(async () => {});
    act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
    await slider();
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(store.readView().viewers[1]?.clock).toMatchObject({ playing: true });
    // Closing a pane takes its viewer out of the view as the viewer unmounts.
    act(() => {
      store.setView((shown) => ({ ...shown, viewers: {} }));
      view.unmount();
    });
    expect(store.readView().viewers[1]).toBeUndefined();
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
      act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
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

  /** A window that joined a session whose presenter plays frame 10 of "a" (400 ms in) 400 ms of backend time ago. */
  function follower(access: "watch" | "explore") {
    const room = FakeSocket.host(access);
    room.setItems([video("a")]);
    FakeSocket.clockOffsetUs = 5_000_000;
    const serverNow = 10_000 * 1000 + FakeSocket.clockOffsetUs;
    const clock = { frame: 10, playing: true, speedCenti: 100, atUs: serverNow - 400_000 };
    room.presenter = { ...room.presenter, ...viewing(["a", { clock }]) };
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

  /** The stream can open before the backend's clock has answered; the follower then moves on to the right frame. */
  const settled = async (frame: string) => {
    const thumb = await slider();
    await waitFor(() => expect(thumb.value).toBe(frame));
  };

  it("follows the presenter's clock by the backend's time, and a watcher has no controls of its own", async () => {
    follower("watch");
    await settled("20");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect((await slider()).disabled).toBe(true);
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
      act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
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
      act(() => FakeSocket.rooms.get("room-1")!.setItems([video("a")]));
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
      act(() => exportOf("a")?.());
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
