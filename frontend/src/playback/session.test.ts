// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { CommandRefused } from "../errors";
import { flush, opened, setup, type OpenedMessage } from "./fakePlayback";
import { PLAYBACK_PROTOCOL } from "./protocol";

describe("opening", () => {
  it("asks for the video, shows indexing, then shows the first frame paused", async () => {
    const { session, server, sent, presented, refresh } = await setup();
    expect(sent).toEqual([{ type: "Open", ticket: "ticket-1", data_bytes: expect.any(Number) }]);
    server.say({ type: "Indexing", progress: 0.4 });
    expect(session.getSnapshot()).toMatchObject({ status: "indexing" });

    server.open();
    expect(session.getSnapshot()).toMatchObject({ status: "ready", frameCount: 10 });
    server.frames(0);
    refresh();
    expect(presented).toEqual([0]);
    expect(session.getSnapshot()).toMatchObject({
      playing: false,
      shown: { sequenceId: 0, timestampUs: 0, periodUs: 40_000 },
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
      ...(opened() as OpenedMessage),
      protocol: PLAYBACK_PROTOCOL + 1,
    });
    expect(session.getSnapshot().status).toBe("failed");
  });
});

describe("cached frames", () => {
  it("reports the frames the backend holds decoded, and none once the connection is lost", async () => {
    const { session, server } = await setup();
    server.open();
    expect(session.getSnapshot().cached).toEqual([]);
    server.say({
      type: "Cached",
      ranges: [
        [0, 3],
        [7, 9],
      ],
    });
    expect(session.getSnapshot().cached).toEqual([
      [0, 3],
      [7, 9],
    ]);
    server.drop();
    expect(session.getSnapshot().cached).toEqual([]);
  });
});

describe("autoplay", () => {
  it("plays from the first frame once it is shown", async () => {
    const { session, server, presented, intents, refresh } = await setup(undefined, {
      autoplay: true,
    });
    server.open();
    expect(session.getSnapshot().playing).toBe(false);
    server.frames(0, 1, 2);
    refresh();
    expect(session.getSnapshot().playing).toBe(true);
    expect(intents.at(-1)).toMatchObject({ frame: 0, playing: true });
    refresh(40);
    expect(presented).toEqual([0, 1]);
  });

  it("finishes playing a one-frame video that is already on screen", async () => {
    const { session, server, intents, refresh } = await setup(undefined, { autoplay: true });
    server.open(1);
    server.frames(0);
    refresh();
    expect(session.getSnapshot().playing).toBe(true);
    refresh(40);
    expect(session.getSnapshot()).toMatchObject({ playing: false, shown: { sequenceId: 0 } });
    expect(intents.at(-1)).toMatchObject({ playing: false });
  });

  it("does not play after the viewer acted before the first frame came", async () => {
    const { session, server, refresh } = await setup(undefined, { autoplay: true });
    server.open();
    session.seek(2);
    server.frames(2);
    refresh();
    expect(session.getSnapshot()).toMatchObject({ playing: false, shown: { sequenceId: 2 } });
  });
});

describe("resuming", () => {
  it("opens where an earlier session left off, paused, at the speed it had", async () => {
    const { session, server, sent, presented, refresh } = await setup({ frame: 7, speed: 2 });
    expect(sent).toEqual([
      { type: "Open", ticket: "ticket-1", start_frame: 7, data_bytes: expect.any(Number) },
    ]);
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
      {
        type: "Want",
        playhead: 0,
        ahead: 4,
        behind: 5,
        direction: "forward",
        data_ahead: 0,
        data_behind: 0,
      },
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

  it("shrinks its window when its budget shrinks and regrows it, asking the backend each time", async () => {
    const { session, server, wants } = await setup();
    server.open();
    expect(wants().at(-1)).toMatchObject({ ahead: 4, behind: 5 });
    session.setBudget(500);
    expect(wants().at(-1)).toMatchObject({ playhead: 0, ahead: 2, behind: 2 });
    session.setBudget(1000);
    expect(wants().at(-1)).toMatchObject({ playhead: 0, ahead: 4, behind: 5 });
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

describe("seeking while playing", () => {
  it("keeps the clock running from the chosen frame", async () => {
    const { session, server, refresh } = await setup();
    server.open();
    server.frames(0, 1, 2);
    refresh();
    session.play();
    refresh(500);
    session.seek(5);
    expect(session.getSnapshot()).toMatchObject({ playing: true, playhead: 5 });
  });
});

describe("speed shortcuts", () => {
  it("step by 0.1 and stop at 0.1", async () => {
    const { session, server } = await setup({ frame: 0, speed: 0.15 });
    server.open();
    session.stepSpeed(-1);
    session.stepSpeed(-1);
    expect(session.getSnapshot().speed).toBe(0.1);
    session.stepSpeed(1);
    expect(session.getSnapshot().speed).toBe(0.2);
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

  it("shows the reason for a frame sought back from a later frame on screen", async () => {
    const { session, server, refresh } = await setup();
    server.open();
    server.frames(0, 3);
    server.say({
      type: "FrameError",
      sequence_id: 2,
      code: "DecodeFailed",
      params: {},
      reason: "corrupt slice",
    });
    refresh();
    session.seek(3);
    refresh();
    session.seek(2);
    refresh();
    expect(session.getSnapshot()).toMatchObject({
      shown: { sequenceId: 3 },
      frameError: { sequenceId: 2, reason: "corrupt slice" },
    });
  });

  it("stops playing on a failed frame instead of skipping it, and keeps its reason on screen", async () => {
    const { session, server, intents, refresh } = await setup();
    server.open();
    server.frames(0, 1, 3);
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
    refresh(40);
    refresh(80);
    expect(session.getSnapshot()).toMatchObject({
      playing: false,
      playhead: 2,
      shown: { sequenceId: 1 },
      frameError: { sequenceId: 2, reason: "truncated" },
    });
    expect(intents.at(-1)).toEqual({ frame: 2, playing: false, speed: 1 });
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
    expect(sent.at(-1)).toEqual({
      type: "Open",
      ticket: "ticket-2",
      start_frame: 3,
      data_bytes: expect.any(Number),
    });
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

describe("taking over an owner's clock", () => {
  const left = { frame: 0, playing: true, speedCenti: 100, atUs: 0 };

  it("plays on from it, and stays the owner: it stops on its own at the end and after a reconnection", async () => {
    const { session, server, intents, retries, refresh } = await setup();
    server.open(3);
    server.frames(0, 1, 2);
    refresh();
    session.restore(left, () => 0);
    expect(session.getSnapshot()).toMatchObject({ playing: true, playhead: 0 });
    refresh(200);
    expect(session.getSnapshot().playing).toBe(false);
    expect(intents.at(-1)).toMatchObject({ frame: 2, playing: false });

    session.restore(left, () => 0);
    intents.length = 0;
    server.drop();
    retries[0].run();
    await flush();
    server.open(3);
    expect(session.getSnapshot().playing).toBe(false);
    expect(intents.at(-1)).toMatchObject({ playing: false });
  });

  it("stops it when the video does not open, unless the clock is a presenter's", async () => {
    const error = { type: "Error", code: "NotAVideo", params: {}, reason: "no" } as const;
    const owner = await setup({ frame: 4, speed: 1 });
    owner.session.restore({ ...left, frame: 4 }, () => 0);
    owner.server.say(error);
    expect(owner.intents).toEqual([{ frame: 4, playing: false, speed: 1 }]);

    const follower = await setup({ frame: 4, speed: 1 });
    follower.session.follow({ ...left, frame: 4 }, () => 0);
    follower.server.say(error);
    expect(follower.intents).toEqual([]);
  });
});
