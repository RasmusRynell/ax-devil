import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import type { ClientMessage, LaneInfo, ServerMessage } from "./protocol";
import type { VideoViewerProps } from "./VideoViewer";
import {
  Viewer,
  answer,
  flushFrames,
  installViewerHarness,
  slider,
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
    expect(room.seats[1]?.view?.viewers.a?.clock).toMatchObject({ frame: 70 });
  });

  describe("lanes", () => {
    const LANES: LaneInfo[] = [
      { label: "DPM", source: 0 },
      { label: "GT", source: 1 },
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
      fireEvent.click(screen.getByRole("switch", { name: "Sticky" }));
      expect(sent.at(-1)).toEqual({
        type: "LanePolicy",
        lane: 1,
        revision: 2,
        policy: { basis: null, tolerance_ms: 50, sticky: { enabled: true, timeout_ms: 2050 } },
        subscribed: true,
      });
      expect(onLaneMatching).toHaveBeenCalledTimes(1);
      expect(onLaneMatching).toHaveBeenCalledWith(1, (sent.at(-1) as { policy: unknown }).policy);
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
