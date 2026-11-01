// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { LaneInfo, LaneStatusWire } from "./protocol";
import { opened as openedMessage } from "./fakeOpened";
import { preparedDrawing } from "./fakeOverlay";
import { PlaybackSession } from "./session";
import {
  PLAYBACK_PROTOCOL,
  type ClientMessage,
  type ServerMessage,
  type VideoFrame,
} from "./protocol";
import type { Incoming } from "./wire";

const FRAME_BYTES = 100;

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

const opened = (frames = 10, lanes?: LaneInfo[]): ServerMessage =>
  openedMessage(frames, FRAME_BYTES, lanes);

/** A session against a fake server, a fake display refresh and a fake clock, with a window of ten frames. */
function setup(resume?: { frame: number; speed: number }) {
  const sent: ClientMessage[] = [];
  const presented: number[] = [];
  const drawn: { sequenceId: number; overlays: unknown[]; atlas: number }[] = [];
  const time = { now: 1000 };
  const refreshes: (() => void)[] = [];
  const retries: { run: () => void; ms: number }[] = [];
  let deliver: (incoming: Incoming) => void = () => {};
  let connections = 0;
  let closedConnections = 0;
  const session = new PlaybackSession("video-1", {
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
  session.setSurface({
    present: (frame, overlays, atlas) => {
      presented.push(frame.sequenceId);
      drawn.push({
        sequenceId: frame.sequenceId,
        overlays: [...overlays],
        atlas: atlas.generation,
      });
    },
  });
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
    drop: () => deliver({ type: "closed" }),
    open: (frames = 10, lanes?: LaneInfo[]) => {
      server.say(opened(frames, lanes));
      server.timeline(frames);
    },
    status: (lane: number, status: LaneStatusWire) =>
      server.say({ type: "LaneStatus", lane, status }),
    overlay: (lane: number, sequenceId: number, revision: number, drawing = preparedDrawing()) =>
      deliver({
        type: "overlays",
        overlays: [{ lane, sequenceId, revision, prepared: drawing }],
      }),
  };
  /** One display refresh after `ms` milliseconds. */
  const refresh = (ms = 0) => {
    time.now += ms;
    // Display refresh callbacks run once, as requestAnimationFrame's do.
    refreshes.splice(0).forEach((callback) => callback());
  };
  const wants = () => sent.filter((message) => message.type === "Want");
  session.start();
  return {
    session,
    server,
    sent,
    presented,
    drawn,
    refresh,
    wants,
    retries,
    refreshes,
    connections: () => connections,
    closedConnections: () => closedConnections,
  };
}

describe("opening", () => {
  it("asks for the video, shows indexing, then shows the first frame paused", () => {
    const { session, server, sent, presented, refresh } = setup();
    expect(sent).toEqual([{ type: "Open", content_id: "video-1" }]);
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

  it("reports the backend's error and neither retries nor draws", () => {
    const { session, server, retries, refreshes } = setup();
    server.say({ type: "Error", reason: "not a video", location: "/clips/a.mp4" });
    server.drop();
    expect(session.getSnapshot()).toMatchObject({
      status: "failed",
      failure: { reason: "not a video", location: "/clips/a.mp4" },
    });
    expect(retries).toEqual([]);
    expect(refreshes).toEqual([]);
  });

  it("refuses a backend that speaks another protocol", () => {
    const { session, server } = setup();
    server.say({
      ...(opened() as Extract<ServerMessage, { type: "Opened" }>),
      protocol: PLAYBACK_PROTOCOL + 1,
    });
    expect(session.getSnapshot().status).toBe("failed");
  });
});

describe("resuming", () => {
  it("opens where an earlier session left off, paused, at the speed it had", () => {
    const { session, server, sent, presented, refresh } = setup({ frame: 7, speed: 2 });
    expect(sent).toEqual([{ type: "Open", content_id: "video-1", start_frame: 7 }]);
    server.open();
    server.frames(7);
    refresh();
    expect(presented).toEqual([7]);
    expect(session.getSnapshot()).toMatchObject({ playhead: 7, speed: 2, playing: false });
    session.seek(3);
    session.setSpeed(0.5);
    expect(session.resumeState()).toEqual({ frame: 3, speed: 0.5 });
  });
});

describe("what the window asks for and keeps", () => {
  it("reports the window split by direction, and keeps only frames inside it", () => {
    const { session, server, wants, presented, refresh } = setup();
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

  it("asks for no display refreshes while paused and idle, and again when something changes", () => {
    const { session, server, refresh, refreshes } = setup();
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

  it("collapses rapid steps to the latest target in one request", () => {
    const { session, server, wants, refresh } = setup();
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
  it("finishes on the last frame and plays again from the start", () => {
    const { session, server, presented, refresh, refreshes } = setup();
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
  it("shows the reason while the screen waits on the frame, and moves on when the next frame is stepped to", () => {
    const { session, server, refresh } = setup();
    server.open();
    server.frames(0, 1, 3);
    server.say({ type: "FrameError", sequence_id: 2, reason: "corrupt slice" });
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

  it("finishes playing when the last frame failed instead of waiting for it", () => {
    const { session, server, refresh } = setup();
    server.open(3);
    server.frames(0, 1);
    server.say({ type: "FrameError", sequence_id: 2, reason: "truncated" });
    refresh();
    session.play();
    refresh();
    refresh(1000);
    expect(session.getSnapshot()).toMatchObject({ playing: false, shown: { sequenceId: 1 } });
  });
});

describe("connection loss and closing", () => {
  it("reconnects where it left off, paused, and keeps the speed", () => {
    const { session, server, sent, retries, refreshes, refresh, connections } = setup();
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
    expect(connections()).toBe(2);
    expect(sent.at(-1)).toEqual({ type: "Open", content_id: "video-1", start_frame: 3 });
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

  it("tells the backend, stops drawing and ignores the socket after close", () => {
    const { session, server, sent, presented, refresh, refreshes, retries, closedConnections } =
      setup();
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

const READY: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [],
  warnings: [],
  samples: 5,
};
const TWO_LANES = [
  { label: "DPM", has_overlay: true },
  { label: "GT", has_overlay: true },
];
const laneViews = (sent: ClientMessage[]) => sent.filter((message) => message.type === "LaneView");

describe("lanes", () => {
  it("describes the video and each lane's load for the UI", () => {
    const { session, server } = setup();
    server.open(10, TWO_LANES);
    expect(session.getSnapshot()).toMatchObject({ width: 64, height: 48, warnings: [] });
    expect(session.getSnapshot().lanes.map((lane) => lane.status)).toEqual([
      { status: "Loading" },
      { status: "Loading" },
    ]);
    server.status(0, READY);
    server.say({ type: "LaneProblem", lane: 0, reason: "no such catalog" });
    expect(session.getSnapshot().lanes[0]).toMatchObject({
      status: READY,
      problem: "no such catalog",
    });
    server.say({ type: "LaneProblem", lane: 7, reason: "ignored" });
  });

  it("shows a frame only once every shown lane that loaded has its overlay for it", () => {
    const { server, presented, drawn, refresh } = setup();
    server.open(10, TWO_LANES);
    server.status(0, READY);
    server.status(1, READY);
    server.frames(0);
    server.overlay(0, 0, 0);
    refresh();
    expect(presented).toEqual([]);
    const second = preparedDrawing();
    server.overlay(1, 0, 0, second);
    refresh();
    expect(presented).toEqual([0]);
    expect(drawn[0].overlays).toHaveLength(2);
    expect(drawn[0].overlays[1]).toBe(second);
  });

  it("is never held up by a lane that is loading, failed, hidden or plain", () => {
    const { session, server, presented, refresh } = setup();
    const lanes = [
      ...TWO_LANES,
      { label: "FRCNN", has_overlay: true },
      { label: "v", has_overlay: false },
    ];
    server.open(10, lanes);
    server.status(1, { status: "Failed", reason: "bad file", location: "gt.txt" });
    server.status(2, READY);
    session.setLaneView(2, { visible: false });
    server.frames(0);
    refresh();
    expect(presented).toEqual([0]);
    // Lane 0 is still loading; when it turns ready the next frame waits for it.
    server.status(0, READY);
    session.step(1);
    server.frames(1);
    refresh();
    expect(presented).toEqual([0]);
    server.overlay(0, 1, 0);
    refresh();
    expect(presented).toEqual([0, 1]);
  });

  it("keeps the older revision on screen until the newer one arrives, and ignores one that arrives late", () => {
    const { session, server, sent, drawn, refresh } = setup();
    server.open(10, TWO_LANES);
    server.status(0, READY);
    server.status(1, { status: "Loading" });
    server.frames(0);
    const old = preparedDrawing();
    server.overlay(0, 0, 0, old);
    refresh();
    session.setLaneView(0, { catalog: "built-in:tracking" });
    expect(laneViews(sent)).toMatchObject([
      { lane: 0, revision: 1, view: { catalog: "built-in:tracking" } },
    ]);
    expect(drawn.at(-1)?.overlays[0]).toBe(old);
    const newer = preparedDrawing();
    server.overlay(0, 0, 1, newer);
    expect(drawn.at(-1)?.overlays[0]).toBe(newer);
    server.overlay(0, 0, 0, preparedDrawing());
    expect(drawn.at(-1)?.overlays[0]).toBe(newer);
  });

  it("applies the atlas changes of every overlay, even those it does not keep", () => {
    const { session, server, drawn, refresh } = setup();
    server.open(10, TWO_LANES);
    server.status(0, READY);
    server.frames(0);
    session.setLaneView(1, { visible: false });
    server.overlay(1, 0, 0, preparedDrawing(3, [{ type: "reset", generation: 3 }]));
    server.overlay(0, 0, 0, preparedDrawing(3));
    refresh();
    expect(drawn.at(-1)).toMatchObject({ sequenceId: 0, atlas: 3 });
  });

  it("sends a new revision for a view change, once, and not for a change that changes nothing", () => {
    const { session, server, sent } = setup();
    server.open(10, TWO_LANES);
    session.setLaneView(1, { sticky: { enabled: true, timeout_ms: null, opacity: 0.5 } });
    session.setLaneView(1, { sticky: { enabled: true, timeout_ms: null, opacity: 0.5 } });
    session.setLaneView(1, { visible: false });
    expect(laneViews(sent)).toMatchObject([
      { lane: 1, revision: 1, view: { sticky: { enabled: true }, visible: true } },
      { lane: 1, revision: 2, view: { visible: false } },
    ]);
    expect(session.getSnapshot().lanes[1]).toMatchObject({ revision: 2 });
  });

  it("sends target changes at most once per display refresh, with the last size", () => {
    const { session, server, sent, refresh } = setup();
    server.open(10, TWO_LANES);
    const size = (width: number) => [
      { width, height: 360 },
      { width: 400, height: 300 },
    ];
    session.setLaneTargets(size(500));
    session.setLaneTargets(size(520));
    session.setLaneTargets(size(560.4));
    expect(laneViews(sent)).toEqual([]);
    refresh();
    expect(laneViews(sent)).toMatchObject([
      { lane: 0, revision: 1, view: { target: { width: 560, height: 360 } } },
      { lane: 1, revision: 1, view: { target: { width: 400, height: 300 } } },
    ]);
    session.setLaneTargets(size(560));
    refresh();
    expect(laneViews(sent)).toHaveLength(2);
  });

  it("gives a reconnected backend the views the user had set", () => {
    const { session, server, sent, retries } = setup();
    server.open(10, TWO_LANES);
    session.setLaneView(0, { visible: false });
    server.drop();
    retries[0].run();
    server.open(10, TWO_LANES);
    expect(laneViews(sent)).toMatchObject([
      { lane: 0, revision: 1, view: { visible: false } },
      { lane: 0, revision: 1, view: { visible: false } },
    ]);
  });
});
