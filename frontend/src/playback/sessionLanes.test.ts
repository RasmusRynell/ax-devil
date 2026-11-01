// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { flush, opened, setup, type OpenedMessage } from "./fakePlayback";
import { preparedDrawing } from "./fakeOverlay";
import type { ClientMessage, LaneStatusWire } from "./protocol";

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
  it("describes the video, the catalogs and each lane's load for the UI", async () => {
    const { session, server } = await setup();
    const catalog = { id: "built-in:standard", name: "Standard", description: "" };
    server.say({ ...(opened(10, TWO_LANES) as OpenedMessage), catalogs: [catalog] });
    server.timeline();
    expect(session.getSnapshot()).toMatchObject({
      width: 64,
      height: 48,
      warnings: [],
      catalogs: [catalog],
    });
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

  it("shows a frame only once every shown lane that loaded has its overlay for it", async () => {
    const { server, presented, drawn, refresh } = await setup();
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

  it("is never held up by a lane that is loading, failed, hidden or plain", async () => {
    const { session, server, presented, refresh } = await setup();
    const lanes = [
      ...TWO_LANES,
      { label: "FRCNN", has_overlay: true },
      { label: "v", has_overlay: false },
    ];
    server.open(10, lanes);
    server.status(1, {
      status: "Failed",
      code: "OverlayInvalid",
      params: {},
      reason: "bad file",
      location: "gt.txt",
    });
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

  it("keeps the older revision on screen until the newer one arrives, and ignores one that arrives late", async () => {
    const { session, server, sent, drawn, refresh } = await setup();
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

  it("applies the atlas changes of every overlay, even those it does not keep", async () => {
    const { session, server, drawn, refresh } = await setup();
    server.open(10, TWO_LANES);
    server.status(0, READY);
    server.frames(0);
    session.setLaneView(1, { visible: false });
    server.overlay(1, 0, 0, preparedDrawing(3, [{ type: "reset", generation: 3 }]));
    server.overlay(0, 0, 0, preparedDrawing(3));
    refresh();
    expect(drawn.at(-1)).toMatchObject({ sequenceId: 0, atlas: 3 });
  });

  it("sends a new revision for a view change, once, and not for a change that changes nothing", async () => {
    const { session, server, sent } = await setup();
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

  it("sends target changes at most once per display refresh, with the last size", async () => {
    const { session, server, sent, refresh } = await setup();
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

  it("gives a reconnected backend the views the user had set", async () => {
    const { session, server, sent, retries } = await setup();
    server.open(10, TWO_LANES);
    session.setLaneView(0, { visible: false });
    server.drop();
    retries[0].run();
    await flush();
    server.open(10, TWO_LANES);
    expect(laneViews(sent)).toMatchObject([
      { lane: 0, revision: 1, view: { visible: false } },
      { lane: 0, revision: 1, view: { visible: false } },
    ]);
  });
});
