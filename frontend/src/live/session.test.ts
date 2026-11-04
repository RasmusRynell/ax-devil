// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { LiveFeedStatus, LiveOpenedWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import { catalogDocument, mapBytes, sampleBytes } from "../playback/fakeData";
import { LANE_DEFAULTS } from "../playback/fakeOpened";
import { videoFrame } from "../playback/fakePlayback";
import { PLAYBACK_PROTOCOL, type ClientMessage, type ServerMessage } from "../playback/protocol";
import { decode, type Incoming } from "../playback/wire";
import { EVENT_LOG_CAP } from "./eventLog";
import { LiveSession } from "./session";

const OVERLAY: LiveOpenedWire["lane"] = { label: "Objects", slot: "overlay", source: 0 };
const PLAIN: LiveOpenedWire["lane"] = { label: "Camera", slot: "camera", source: null };

/** A live session against a fake socket, a fake display refresh and fake timers. */
async function setup(lane = OVERLAY) {
  const sent: ClientMessage[] = [];
  const refreshes: (() => void)[] = [];
  const timers: { run: () => void; ms: number }[] = [];
  const drawn: { sequenceId: number; lane: LaneFrame | undefined }[] = [];
  let deliver: (incoming: Incoming) => void = () => {};
  let connections = 0;
  const session = new LiveSession({
    ticket: async () => "ticket",
    connect: (onIncoming) => {
      connections++;
      deliver = onIncoming;
      return { send: (message) => sent.push(message), close: () => {} };
    },
    frame: (callback) => {
      refreshes.push(callback);
      return () => void refreshes.splice(refreshes.indexOf(callback) >>> 0, 1);
    },
    after: (run, ms) => {
      const timer = { run, ms };
      timers.push(timer);
      return () => void timers.splice(timers.indexOf(timer) >>> 0, 1);
    },
  });
  session.setSurface({
    present: (frame, lanes) => drawn.push({ sequenceId: frame.sequenceId, lane: lanes[0] }),
  });
  const say = (message: ServerMessage) => deliver({ type: "message", message });
  const server = {
    say,
    open: () =>
      say({
        type: "LiveOpened",
        protocol: PLAYBACK_PROTOCOL,
        lane,
        lane_defaults: LANE_DEFAULTS,
        catalogs: [],
      }),
    status: (video: LiveFeedStatus, overlay: LiveFeedStatus | null = null, behind = false) =>
      say({ type: "LiveStatus", video, overlay, behind }),
    /** The overlay lane has loaded, with Standard's document, so what it draws can be told. */
    ready: () => {
      say({
        type: "LaneStatus",
        lane: 0,
        status: {
          status: "Ready",
          bases: ["Time"],
          default_basis: "Time",
          filter: [],
          warnings: [],
          samples: 0,
          data_revision: 1,
          alignment: null,
          mean_sample_bytes: 10,
          ingest: { state: "Complete" },
        },
      });
      say({
        type: "Catalog",
        id: "built-in:standard",
        revision: 1,
        document: catalogDocument(),
        problem: null,
      });
    },
    frame: (id: number) =>
      deliver({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(id) } }),
    /** A Sample Item, then the cell that gives it to `frame`. */
    sample: (index: number, frame: number) => {
      for (const bytes of [
        sampleBytes({ sample: index }),
        mapBytes({ first: frame, cells: [index] }),
      ]) {
        const incoming = decode(bytes);
        if (incoming) deliver(incoming);
      }
    },
    events: (count: number, label = "Rename 1 → 2") =>
      say({
        type: "LiveEvents",
        frame: 1,
        samples: [
          {
            time_us: 1_700_000_000_000_000,
            events: Array.from({ length: count }, () => ({
              kind: "rename" as const,
              label,
              entities: ["1", "2"],
            })),
          },
        ],
      }),
    drop: (code = 1006) => deliver({ type: "closed", code }),
  };
  /** One display refresh. */
  const refresh = () => refreshes.splice(0).forEach((callback) => callback());
  session.start();
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  server.open();
  return { session, server, sent, drawn, refresh, timers, connections: () => connections };
}

const live = (sent: ClientMessage[]) => sent.filter((message) => message.type === "Live");

describe("live session", () => {
  it("connects only when started, opens with a ticket and ends with Close", async () => {
    const { session, sent } = await setup();
    expect(sent[0]).toMatchObject({ type: "Open", ticket: "ticket" });
    expect(session.getSnapshot().status).toBe("ready");
    session.close();
    expect(sent.at(-1)).toEqual({ type: "Close" });
  });

  it("reports each feed as the backend says and publishes nothing for a repeat", async () => {
    const { session, server } = await setup();
    const states: LiveFeedStatus[] = [];
    session.subscribe(() => states.push(session.getSnapshot().video));
    server.status({ status: "Live" }, { status: "Connecting" });
    server.status({ status: "Live" }, { status: "Connecting" });
    server.status(
      { status: "Reconnecting", attempt: 2, reason: "Unreachable" },
      { status: "Failed", reason: "BrokerBadCredentials" },
      true,
    );
    expect(states).toEqual([
      { status: "Live" },
      { status: "Reconnecting", attempt: 2, reason: "Unreachable" },
    ]);
    const view = session.getSnapshot();
    expect(view.overlay).toEqual({ status: "Failed", reason: "BrokerBadCredentials" });
    expect(view.behind).toBe(true);
  });

  it("draws a frame once its overlay data has arrived, and without it when that is late", async () => {
    const { server, refresh, drawn, timers } = await setup();
    server.ready();
    server.frame(1);
    refresh();
    // The lane waits for the cell of frame 1: nothing is drawn yet.
    expect(drawn).toEqual([]);
    server.sample(0, 1);
    refresh();
    expect(drawn.map(({ sequenceId, lane }) => [sequenceId, lane !== undefined])).toEqual([
      [1, true],
    ]);
    server.frame(2);
    refresh();
    expect(drawn).toHaveLength(1);
    // Nothing comes for frame 2: after a moment it is shown as it is, which is how a silent overlay never freezes the video.
    timers.splice(0).forEach(({ run }) => run());
    refresh();
    expect(drawn.map(({ sequenceId, lane }) => [sequenceId, lane !== undefined])).toEqual([
      [1, true],
      [2, false],
    ]);
  });

  it("draws a plain lane's frames at once, the newest of those that arrived", async () => {
    const { server, refresh, drawn } = await setup(PLAIN);
    server.frame(1);
    server.frame(2);
    refresh();
    expect(drawn.map(({ sequenceId }) => sequenceId)).toEqual([2]);
  });

  it("keeps the newest entries of the Events list, counts what it dropped and marks gaps", async () => {
    const { session, server } = await setup();
    // Nothing shows the list, and it records all the same.
    server.events(EVENT_LOG_CAP - 1);
    server.say({ type: "LiveGap", time_us: 1_700_000_000_500_000 });
    expect(session.events.getSnapshot().dropped).toBe(0);
    server.events(3, "Merge 3 → 4");
    const { entries, dropped } = session.events.getSnapshot();
    expect(entries).toHaveLength(EVENT_LOG_CAP);
    expect(dropped).toBe(3);
    expect(entries.filter((entry) => entry.type === "gap")).toHaveLength(1);
    expect(entries.at(-1)).toMatchObject({ type: "event", label: "Merge 3 → 4" });
  });

  it("pauses without dropping events or the connection, and resumes from the next frame", async () => {
    const { session, server, sent, refresh, drawn } = await setup(PLAIN);
    server.frame(1);
    refresh();
    session.pause();
    expect(live(sent)).toEqual([{ type: "Live", action: "pause" }]);
    expect(session.getSnapshot().paused).toBe(true);
    // What was on its way when the pause was asked for does not change the picture; events still count.
    server.frame(2);
    server.events(2);
    refresh();
    expect(drawn.map(({ sequenceId }) => sequenceId)).toEqual([1]);
    expect(session.events.getSnapshot().entries).toHaveLength(2);
    session.resume();
    expect(live(sent).at(-1)).toEqual({ type: "Live", action: "resume" });
    server.frame(3);
    refresh();
    expect(drawn.map(({ sequenceId }) => sequenceId)).toEqual([1, 3]);
    expect(session.getSnapshot().paused).toBe(false);
  });

  it("asks the backend to retry, and a resume that follows no pause sends nothing", async () => {
    const { session, sent } = await setup();
    session.retry();
    expect(live(sent)).toEqual([{ type: "Live", action: "retry" }]);
    session.resume();
    expect(live(sent)).toHaveLength(1);
  });

  it("shows recovered frames after a retry that follows a pause", async () => {
    const { session, server, refresh, drawn } = await setup(PLAIN);
    session.pause();
    session.retry();
    expect(session.getSnapshot().paused).toBe(false);
    server.frame(2);
    refresh();
    expect(drawn.map(({ sequenceId }) => sequenceId)).toEqual([2]);
  });

  it("shows frames again, without a message, when the window begins following a presenter", async () => {
    const { session, server, sent, refresh, drawn } = await setup(PLAIN);
    session.pause();
    session.follow();
    expect(session.getSnapshot().paused).toBe(false);
    expect(live(sent)).toEqual([{ type: "Live", action: "pause" }]);
    server.frame(2);
    refresh();
    expect(drawn.map(({ sequenceId }) => sequenceId)).toEqual([2]);
  });

  it("reopens a lost socket with the Events list kept, and ends for good when access is withdrawn", async () => {
    const { session, server, timers, connections } = await setup();
    server.events(2);
    server.drop();
    expect(session.getSnapshot().status).toBe("reconnecting");
    timers.splice(0).forEach(({ run }) => run());
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(connections()).toBe(2);
    server.open();
    expect(session.getSnapshot().status).toBe("ready");
    expect(session.events.getSnapshot().entries).toHaveLength(2);
    server.drop(4403);
    expect(session.getSnapshot().status).toBe("failed");
  });
});
