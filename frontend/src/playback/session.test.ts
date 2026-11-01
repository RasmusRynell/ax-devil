// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { CommandRefused } from "../errors";
import { PlaybackSession, type PlaybackIntent } from "./session";
import {
  PLAYBACK_PROTOCOL,
  type ClientMessage,
  type ServerMessage,
  type VideoFrame,
} from "./protocol";
import type { Incoming } from "./wire";

const FRAME_BYTES = 100;

/** Lets the ticket the session asked for arrive, so it can open its socket. */
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function videoFrame(sequenceId: number): VideoFrame {
  const empty = new Uint8Array(0);
  return {
    sequenceId,
    timestampUs: sequenceId * 40_000,
    keyframe: false,
    last: false,
    width: 4,
    height: 4,
    format: "i420",
    matrix: "bt709",
    fullRange: false,
    strides: [4, 2, 2],
    planes: [empty, empty, empty],
    byteLength: FRAME_BYTES,
  };
}

const opened = (frames = 10): ServerMessage => ({
  type: "Opened",
  protocol: PLAYBACK_PROTOCOL,
  frame_count: frames,
  duration_us: frames * 40_000,
  frame_bytes: FRAME_BYTES,
});

/** A session against a fake server, a fake display refresh and a fake clock, with a window of ten frames. */
async function setup(resume?: { frame: number; speed: number }) {
  const sent: ClientMessage[] = [];
  const intents: PlaybackIntent[] = [];
  let tickets = 0;
  let ticket: () => Promise<string> = async () => `ticket-${++tickets}`;
  const presented: number[] = [];
  const time = { now: 1000 };
  const refreshes: (() => void)[] = [];
  const retries: { run: () => void; ms: number }[] = [];
  let deliver: (incoming: Incoming) => void = () => {};
  let connections = 0;
  let closedConnections = 0;
  const session = new PlaybackSession({
    ticket: () => ticket(),
    onIntent: (intent) => intents.push(intent),
    connect: (onIncoming) => {
      connections++;
      deliver = onIncoming;
      return { send: (message) => sent.push(message), close: () => closedConnections++ };
    },
    now: () => time.now,
    frame: (callback) => {
      refreshes.push(callback);
      return () => void refreshes.splice(refreshes.indexOf(callback) >>> 0, 1);
    },
    after: (run, ms) => {
      const retry = { run, ms };
      retries.push(retry);
      return () => void retries.splice(retries.indexOf(retry) >>> 0, 1);
    },
    budgetBytes: 10 * FRAME_BYTES,
    resume,
  });
  session.setSurface({ present: (frame) => presented.push(frame.sequenceId) });
  const server = {
    say: (message: ServerMessage) => deliver({ type: "message", message }),
    frames: (...ids: number[]) =>
      ids.forEach((id) =>
        deliver({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(id) } }),
      ),
    timeline: (frames = 10) =>
      deliver({
        type: "envelope",
        envelope: {
          kind: "timeline",
          times: Float64Array.from({ length: frames }, (_, id) => id * 40_000),
        },
      }),
    drop: (code = 1006) => deliver({ type: "closed", code }),
    open: (frames = 10) => {
      server.say(opened(frames));
      server.timeline(frames);
    },
  };
  /** One display refresh after `ms` milliseconds. */
  const refresh = (ms = 0) => {
    time.now += ms;
    // Display refresh callbacks run once, as requestAnimationFrame's do.
    refreshes.splice(0).forEach((callback) => callback());
  };
  const wants = () => sent.filter((message) => message.type === "Want");
  session.start();
  await flush();
  return {
    session,
    server,
    sent,
    intents,
    tickets: (next: () => Promise<string>) => (ticket = next),
    presented,
    refresh,
    wants,
    retries,
    refreshes,
    connections: () => connections,
    closedConnections: () => closedConnections,
  };
}

describe("opening", () => {
  it("asks for the video, shows indexing, then shows the first frame paused", async () => {
    const { session, server, sent, presented, refresh } = await setup();
    expect(sent).toEqual([{ type: "Open", ticket: "ticket-1" }]);
    server.say({ type: "Indexing", progress: 0.4 });
    expect(session.getSnapshot()).toMatchObject({ status: "indexing", progress: 0.4 });

    server.open();
    expect(session.getSnapshot()).toMatchObject({ status: "ready", frameCount: 10 });
    server.frames(0);
    refresh();
    expect(presented).toEqual([0]);
    expect(session.getSnapshot()).toMatchObject({
      playing: false,
      shown: { sequenceId: 0, timestampUs: 0 },
    });
  });

  it("reports the backend's error and neither retries nor draws", async () => {
    const { session, server, retries, refreshes } = await setup();
    server.say({
      type: "Error",
      code: "NotAVideo",
      params: {},
      reason: "not a video",
      location: "/clips/a.mp4",
    });
    server.drop();
    expect(session.getSnapshot()).toMatchObject({
      status: "failed",
      failure: { reason: "not a video", location: "/clips/a.mp4" },
    });
    expect(retries).toEqual([]);
    expect(refreshes).toEqual([]);
  });

  it("refuses a backend that speaks another protocol", async () => {
    const { session, server } = await setup();
    server.say({
      ...(opened() as Extract<ServerMessage, { type: "Opened" }>),
      protocol: PLAYBACK_PROTOCOL + 1,
    });
    expect(session.getSnapshot().status).toBe("failed");
  });
});

describe("resuming", () => {
  it("opens where an earlier session left off, paused, at the speed it had", async () => {
    const { session, server, sent, presented, refresh } = await setup({ frame: 7, speed: 2 });
    expect(sent).toEqual([{ type: "Open", ticket: "ticket-1", start_frame: 7 }]);
    server.open();
    server.frames(7);
    refresh();
    expect(presented).toEqual([7]);
    expect(session.getSnapshot()).toMatchObject({ playhead: 7, speed: 2, playing: false });
    session.seek(3);
    session.setSpeed(0.5);
    expect(session.resumeState()).toEqual({ frame: 3, speed: 0.5 });
  });

  it("keeps the position when the stream is revoked, for the replacement a seat restart opens", async () => {
    const { session, server } = await setup();
    server.open();
    session.seek(6);
    server.drop(4403);
    expect(session.getSnapshot().status).toBe("failed");
    expect(session.resumeState()).toEqual({ frame: 6, speed: 1 });
  });
});

describe("what the window asks for and keeps", () => {
  it("reports the window split by direction, and keeps only frames inside it", async () => {
    const { session, server, wants, presented, refresh } = await setup();
    server.open();
    expect(wants()).toEqual([
      { type: "Want", playhead: 0, ahead: 4, behind: 5, direction: "forward" },
    ]);
    // Paused at 0 the window is 0 to 4: frame 6 is dropped on arrival.
    server.frames(0, 1, 6);
    refresh();
    session.play();
    refresh();
    expect(wants().at(-1)).toMatchObject({ ahead: 7, behind: 2, playhead: 0 });
    // Frame 6 is inside the playing window now, but it was not kept, so it is shown only once delivered again.
    refresh(240);
    expect(presented).toEqual([0, 1]);
    server.frames(6);
    refresh(0);
    expect(presented.at(-1)).toBe(6);
  });

  it("asks for no display refreshes while paused and idle, and again when something changes", async () => {
    const { session, server, refresh, refreshes } = await setup();
    server.open();
    expect(refreshes).toHaveLength(0);
    server.frames(0);
    refresh();
    expect(refreshes).toHaveLength(0);
    session.step(1);
    expect(refreshes).toHaveLength(1);
    refresh();
    expect(refreshes).toHaveLength(0);
    server.frames(1);
    expect(refreshes).toHaveLength(1);
    refresh();
    session.play();
    refresh(40);
    expect(refreshes).toHaveLength(1);
    session.pause();
    refresh();
    expect(refreshes).toHaveLength(0);
  });

  it("collapses rapid steps to the latest target in one request", async () => {
    const { session, server, wants, refresh } = await setup();
    server.open();
    server.frames(0);
    refresh();
    const before = wants().length;
    session.step(1);
    session.step(1);
    session.step(1);
    refresh();
    expect(wants().length).toBe(before + 1);
    expect(wants().at(-1)).toMatchObject({ playhead: 3 });
  });
});

describe("playing to the end", () => {
  it("finishes on the last frame and plays again from the start", async () => {
    const { session, server, presented, refresh, refreshes } = await setup();
    server.open(3);
    server.frames(0, 1, 2);
    refresh();
    session.play();
    refresh();
    refresh(200);
    expect(session.getSnapshot()).toMatchObject({ playing: false, shown: { sequenceId: 2 } });
    expect(refreshes).toHaveLength(0);
    session.play();
    expect(refreshes).toHaveLength(1);
    server.frames(0);
    refresh();
    expect(presented.at(-1)).toBe(0);
    expect(session.getSnapshot().playing).toBe(true);
  });
});

describe("frames that failed to decode", () => {
  it("shows the reason while the screen waits on the frame, and moves on when the next frame is stepped to", async () => {
    const { session, server, refresh } = await setup();
    server.open();
    server.frames(0, 1, 3);
    server.say({
      type: "FrameError",
      sequence_id: 2,
      code: "DecodeFailed",
      params: {},
      reason: "corrupt slice",
    });
    refresh();
    session.seek(2);
    refresh();
    expect(session.getSnapshot()).toMatchObject({
      shown: { sequenceId: 0 },
      frameError: { sequenceId: 2, reason: "corrupt slice" },
    });
    session.step(1);
    refresh();
    expect(session.getSnapshot()).toMatchObject({
      shown: { sequenceId: 3 },
      frameError: undefined,
    });
  });

  it("finishes playing when the last frame failed instead of waiting for it", async () => {
    const { session, server, refresh } = await setup();
    server.open(3);
    server.frames(0, 1);
    server.say({
      type: "FrameError",
      sequence_id: 2,
      code: "DecodeFailed",
      params: {},
      reason: "truncated",
    });
    refresh();
    session.play();
    refresh();
    refresh(1000);
    expect(session.getSnapshot()).toMatchObject({ playing: false, shown: { sequenceId: 1 } });
  });
});

describe("connection loss and closing", () => {
  it("reconnects where it left off, paused, and keeps the speed", async () => {
    const { session, server, sent, retries, refreshes, refresh, connections } = await setup();
    server.open();
    server.frames(0, 1, 2, 3);
    refresh();
    session.setSpeed(2);
    session.seek(3);
    refresh();
    server.drop();
    expect(session.getSnapshot().status).toBe("reconnecting");
    expect(refreshes).toEqual([]);
    expect(retries).toHaveLength(1);

    retries[0].run();
    await flush();
    expect(connections()).toBe(2);
    // A ticket works once: the new connection spends a new one.
    expect(sent.at(-1)).toEqual({ type: "Open", ticket: "ticket-2", start_frame: 3 });
    server.open();
    server.frames(3);
    refresh();
    expect(session.getSnapshot()).toMatchObject({
      status: "ready",
      speed: 2,
      playing: false,
      shown: { sequenceId: 3 },
    });
  });

  it("publishes the paused position when a playing presenter reconnects, so followers stop with it", async () => {
    const { session, server, retries, intents, refresh } = await setup();
    server.open();
    server.frames(0, 1, 2, 3);
    refresh();
    session.play();
    expect(intents.at(-1)).toMatchObject({ playing: true });
    server.drop();
    retries[0].run();
    await flush();
    server.open();
    server.frames(0, 1, 2, 3);
    refresh();
    expect(session.getSnapshot().playing).toBe(false);
    expect(intents.at(-1)).toMatchObject({ playing: false });
  });

  it("ends for good when the backend refuses or revokes the stream, without asking for another ticket", async () => {
    for (const code of [4401, 4403]) {
      const { session, server, tickets, retries, connections } = await setup();
      let asked = 0;
      tickets(async () => `ticket-${++asked}`);
      server.open();
      server.drop(code);
      await flush();
      expect(session.getSnapshot()).toMatchObject({ status: "failed" });
      expect(session.getSnapshot().failure?.reason).toBeTruthy();
      expect(retries).toEqual([]);
      expect(asked).toBe(0);
      expect(connections()).toBe(1);
    }
  });

  it("tells the backend, stops drawing and ignores the socket after close", async () => {
    const { session, server, sent, presented, refresh, refreshes, retries, closedConnections } =
      await setup();
    server.open();
    session.close();
    expect(sent.at(-1)).toEqual({ type: "Close" });
    expect(closedConnections()).toBe(1);
    expect(refreshes).toEqual([]);
    server.frames(0);
    server.drop();
    refresh();
    expect(presented).toEqual([]);
    expect(retries).toEqual([]);
  });
});

describe("tickets", () => {
  it("fails when the backend refuses the ticket, and tries again when the session is only offline", async () => {
    const refused = await setup();
    refused.tickets(() => Promise.reject(new CommandRefused("ContentUnavailable")));
    refused.server.drop();
    refused.retries[0].run();
    await flush();
    expect(refused.session.getSnapshot()).toMatchObject({ status: "failed" });
    // Only the retry that asked is there: no other was scheduled.
    expect(refused.retries).toHaveLength(1);

    const offline = await setup();
    offline.tickets(() => Promise.reject(new Error("The connection is down.")));
    offline.server.drop();
    offline.retries[0].run();
    await flush();
    expect(offline.session.getSnapshot().status).toBe("reconnecting");
    expect(offline.retries).toHaveLength(2);
  });
});

describe("following a presenter", () => {
  const intent = { frame: 2, playing: true, speedCenti: 200, atUs: 0 };

  it("puts the clock where the presenter's is by now, at its speed, and keeps in step across a reconnection", async () => {
    const { session, server, intents, retries, refresh } = await setup();
    server.open();
    server.frames(0);
    refresh();
    // 120 ms after the intent, at twice the speed: 240 ms on from frame 2 (80 ms) is frame 8.
    session.follow(intent, () => 120_000);
    expect(session.getSnapshot()).toMatchObject({ playing: true, speed: 2, playhead: 8 });
    session.follow({ ...intent, frame: 5, playing: false }, () => 9_000_000);
    expect(session.getSnapshot()).toMatchObject({ playing: false, playhead: 5 });
    // A follower does not publish; only what its own viewer does becomes an intent.
    expect(intents).toEqual([]);

    session.follow(intent, () => 120_000);
    server.drop();
    retries[0].run();
    await flush();
    server.open();
    expect(session.getSnapshot()).toMatchObject({ playing: true, playhead: 8 });
  });
});
