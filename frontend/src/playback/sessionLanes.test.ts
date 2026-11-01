// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { catalogDocument, EMPTY, NONE } from "./fakeData";
import { flush, opened, setup, type OpenedMessage } from "./fakePlayback";
import type { ClientMessage, LaneInfo, LaneStatusWire } from "./protocol";

const READY: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [],
  warnings: [],
  samples: 5,
  data_revision: 1,
  mean_sample_bytes: 10,
};
const DPM: LaneInfo = { label: "DPM", source: 0 };
const TWO_LANES: LaneInfo[] = [DPM, { label: "GT", source: 1 }];
const policies = (sent: ClientMessage[]) => sent.filter((message) => message.type === "LanePolicy");
const catalogs = (sent: ClientMessage[]) => sent.filter((message) => message.type === "Catalog");

/** Frame 0 shown with a drawn lane: the maps give sample 0 to frames 0 and 1 (frame 1 retained), none to 2, empty to 3. */
async function shown() {
  const t = await setup();
  t.server.open(10, [DPM]);
  t.server.catalog();
  t.server.status(0, READY);
  t.server.frames(0, 1, 2, 3);
  t.refresh();
  t.server.map({ cells: [0, 0, NONE, EMPTY], kinds: [0, 2, 0, 0] });
  t.server.sample();
  t.refresh();
  expect(t.presented).toEqual([0]);
  return t;
}

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
    server.say({ type: "LaneProblem", lane: 0, reason: "bad timeout" });
    expect(session.getSnapshot().lanes[0]).toMatchObject({ status: READY, problem: "bad timeout" });
    server.say({ type: "LaneProblem", lane: 7, reason: "ignored" });
  });

  it("subscribes every source lane when the entry opens and again after a reconnection", async () => {
    const { session, server, sent, retries } = await setup();
    server.open(10, [...TWO_LANES, { label: "v", source: null }]);
    expect(policies(sent)).toMatchObject([
      { lane: 0, revision: 1, subscribed: true, policy: { tolerance_ms: 50 } },
      { lane: 1, revision: 1, subscribed: true },
    ]);
    session.setLane(0, { visible: false });
    server.drop();
    retries[0].run();
    await flush();
    server.open(10, [...TWO_LANES, { label: "v", source: null }]);
    expect(policies(sent).slice(3)).toMatchObject([
      { lane: 0, revision: 3, subscribed: false },
      { lane: 1, revision: 2, subscribed: true },
    ]);
  });

  it("asks for each catalog once, however many lanes use it, and keeps it across a reconnection", async () => {
    const { session, server, sent, retries } = await setup();
    server.open(10, TWO_LANES);
    expect(catalogs(sent)).toEqual([{ type: "Catalog", id: "built-in:standard" }]);
    server.catalog();
    session.setLane(1, { catalog: "built-in:classic" });
    session.setLane(0, { catalog: "built-in:classic" });
    session.setLane(0, { catalog: "built-in:standard" });
    expect(catalogs(sent)).toHaveLength(2);
    server.catalog("built-in:classic", catalogDocument("classic"));
    server.drop();
    retries[0].run();
    await flush();
    server.open(10, TWO_LANES);
    expect(catalogs(sent).map((m) => m.id)).toEqual(["built-in:standard", "built-in:classic"]);
  });

  it("sizes the data window of a Want from what the shown lanes' items weigh", async () => {
    const { session, server, wants, refresh } = await setup();
    server.open(10, TWO_LANES);
    expect(wants().at(-1)).toMatchObject({ data_ahead: 0, data_behind: 0 });
    server.status(0, { ...READY, mean_sample_bytes: 50 });
    refresh();
    // A tenth of the 1000 byte budget is two items: the playhead and one frame before it.
    expect(wants().at(-1)).toMatchObject({ ahead: 4, behind: 5, data_ahead: 0, data_behind: 1 });
    session.setLane(0, { visible: false });
    refresh();
    expect(wants().at(-1)).toMatchObject({ data_ahead: 0, data_behind: 0 });
  });

  it("never draws another sample's marks: it waits for the map, the item and the catalog, and draws them together", async () => {
    const { server, presented, drawn, refresh, retries, session } = await setup();
    server.open(10, [DPM]);
    server.status(0, READY);
    server.frames(0);
    refresh();
    // The video is here; the lane has nothing, so the frame waits for it, but not for long.
    expect(presented).toEqual([]);
    const timer = retries.at(-1);
    expect(timer?.ms).toBeLessThanOrEqual(250);
    refresh(250);
    timer?.run();
    refresh();
    expect(presented).toEqual([0]);
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    expect(session.getSnapshot().lanes[0].pending).toBe(true);
    server.map({ cells: [0] });
    server.sample();
    expect(drawn).toHaveLength(1);
    server.catalog();
    expect(drawn).toHaveLength(2);
    expect(drawn[1].lanes[0]).toMatchObject({ source: 0, dataRevision: 1, sample: 0, opacity: 1 });
    expect(drawn[1].lanes[0]?.columns.rows).toBeGreaterThan(0);
    expect(session.getSnapshot().lanes[0].pending).toBe(false);
  });

  it("shows a frame at once when its lanes have their data, and draws nothing for none or empty", async () => {
    const { session, server, presented, drawn, refresh } = await shown();
    session.step(2);
    refresh();
    expect(presented).toEqual([0, 2]);
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    session.step(1);
    refresh();
    expect(presented).toEqual([0, 2, 3]);
    expect(session.getSnapshot().lanes[0].pending).toBe(false);
    server.say({ type: "FrameError", sequence_id: 9, code: "DecodeFailed", params: {} });
  });

  it("draws a retained sample at the lane's retained opacity", async () => {
    const { session, drawn, refresh } = await shown();
    session.setLane(0, { sticky: { enabled: false, timeout_ms: 2050, opacity: 0.5 } });
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0, opacity: 1 });
    session.step(1);
    refresh();
    expect(drawn.at(-1)).toMatchObject({ sequenceId: 1, lanes: [{ sample: 0, opacity: 0.5 }] });
  });

  it("blanks a lane whose policy changed until maps of the new revision arrive", async () => {
    const { session, server, sent, drawn } = await shown();
    session.setLane(0, { tolerance_ms: 100 });
    expect(policies(sent).at(-1)).toMatchObject({ revision: 2, policy: { tolerance_ms: 100 } });
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    expect(session.getSnapshot().lanes[0]).toMatchObject({ pending: true, policyRevision: 2 });
    server.map({ cells: [0, 0], policyRevision: 1 });
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    server.map({ cells: [0, 0], policyRevision: 2 });
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0 });
    expect(session.getSnapshot().lanes[0].pending).toBe(false);
  });

  it("is never held up by a lane that is loading, failed, hidden or plain", async () => {
    const { session, server, presented, refresh } = await setup();
    server.open(10, [
      DPM,
      { label: "GT", source: 1 },
      { label: "FRCNN", source: 2 },
      { label: "v", source: null },
    ]);
    server.status(1, { status: "Failed", code: "OverlayInvalid", params: {}, reason: "bad file" });
    server.status(2, READY);
    session.setLane(2, { visible: false });
    server.frames(0);
    refresh();
    expect(presented).toEqual([0]);
  });

  it("ends the session on an item it cannot read", async () => {
    const { session, server } = await setup();
    server.open(10, [DPM]);
    server.status(0, READY);
    server.sample({ item: new Uint8Array(64) });
    expect(session.getSnapshot().status).toBe("failed");
  });
});
