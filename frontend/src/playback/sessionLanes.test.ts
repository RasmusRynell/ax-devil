// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { SampleView } from "../present";
import { catalogDocument, EMPTY, NONE } from "./fakeData";
import { LANE_SETTINGS } from "./fakeOpened";
import { flush, opened, setup, type OpenedMessage } from "./fakePlayback";
import type { ClientMessage, LaneInfo, LaneStatusWire } from "./protocol";

const READY: LaneStatusWire = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 5,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 10,
  ingest: { state: "Complete" },
};
const DPM: LaneInfo = { label: "DPM", slot: "DPM", source: 0 };
const TWO_LANES: LaneInfo[] = [DPM, { label: "GT", slot: "GT", source: 1 }];
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
  t.server.window(0, 9);
  t.server.map({ cells: [0, 0, NONE, EMPTY], kinds: [0, 2, 0, 0] });
  // The sample is frame 5 of its file, and the video clock has it at the first frame's time.
  t.server.sample({ timeUs: 0, frame: 5 });
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

  it("holds the revision each lane's catalog compiled from, plain lanes included, and carries the export socket's word to the export", async () => {
    const { session, server, sent } = await setup();
    // The backend lists the catalogs it serves, the built-ins at least.
    const standard = { id: "built-in:standard", name: "Standard", description: "" };
    const lanes = [DPM, { label: "v", slot: "v", source: null }];
    server.say({ ...(opened(10, lanes) as OpenedMessage), catalogs: [standard] });
    server.timeline();
    expect(session.getSnapshot().lanes.map((lane) => lane.catalogRevision)).toEqual([
      undefined,
      undefined,
    ]);
    server.catalog("built-in:standard", catalogDocument(), 4);
    expect(session.getSnapshot().lanes.map((lane) => lane.catalogRevision)).toEqual([4, 4]);
    expect(catalogs(sent)).toHaveLength(1);
    session.exports.start({
      lanes: [],
      first: 2,
      last: 4,
      format: { format: "Png" },
      path: "/a.png",
    });
    server.say({ type: "Export", job: 1, state: { state: "Running", done: 2, total: 3 } });
    expect(session.exports.getSnapshot()).toMatchObject({ phase: "running", done: 2 });
    server.drop();
    expect(session.exports.getSnapshot()).toMatchObject({ phase: "failed" });
  });

  it("subscribes every source lane when the entry opens and again after a reconnection", async () => {
    const { session, server, sent, retries } = await setup();
    server.open(10, [...TWO_LANES, { label: "v", slot: "v", source: null }]);
    expect(policies(sent)).toMatchObject([
      { lane: 0, revision: 1, subscribed: true, policy: { tolerance_ms: 50 } },
      { lane: 1, revision: 1, subscribed: true },
    ]);
    session.setLane(0, { visible: false });
    server.drop();
    retries[0].run();
    await flush();
    server.open(10, [...TWO_LANES, { label: "v", slot: "v", source: null }]);
    expect(policies(sent).slice(3)).toMatchObject([
      { lane: 0, revision: 3, subscribed: false },
      { lane: 1, revision: 2, subscribed: true },
    ]);
  });

  it("asks for each catalog once, however many lanes use it, and asks again on a reconnection to be pushed its changes", async () => {
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
    expect(catalogs(sent).map((m) => m.id)).toEqual([
      "built-in:standard",
      "built-in:classic",
      "built-in:standard",
      "built-in:classic",
    ]);
  });

  it("recompiles a pushed catalog, keeps the last good one while a push fails, and takes a restarted backend's lower revision", async () => {
    const { session, server, drawn } = await shown();
    const name = () => drawn.at(-1)?.lanes[0]?.catalog.name;
    const problem = () => session.getSnapshot().lanes[0].catalogProblem;
    expect(name()).toBe("Standard");
    server.catalog("built-in:standard", catalogDocument("classic"), 2);
    expect([name(), problem()]).toEqual(["Classic", undefined]);
    server.catalog("built-in:standard", '{"schema": 4}', 3);
    expect(name()).toBe("Classic");
    expect(problem()).toMatch(/\(\d+:\d+\)/);
    server.catalog("built-in:standard", catalogDocument(), 1);
    expect([name(), problem()]).toEqual(["Standard", undefined]);
    server.catalog("built-in:standard", null, 0);
    expect([name(), problem()]).toEqual(["Standard", "There is no catalog built-in:standard"]);
  });

  it("lists the catalogs the backend pushes", async () => {
    const { session, server } = await setup();
    server.open(10, [DPM]);
    const listing = [{ id: "user:night.json", name: "Night", description: "" }];
    server.say({ type: "Catalogs", catalogs: listing });
    expect(session.getSnapshot().catalogs).toEqual(listing);
  });

  it("never leaves a lane without a catalog: one that fails draws with the lane's last, else the default", async () => {
    const { session, server, drawn } = await shown();
    session.setLane(0, { catalog: "user:broken.json" });
    server.catalog("user:broken.json", '{"schema": 4}');
    expect(drawn.at(-1)?.lanes[0]?.catalog.name).toBe("Standard");
    expect(session.getSnapshot().lanes[0].catalogProblem).toBeDefined();
    session.setLane(0, { catalog: "user:other.json" });
    server.catalog("user:other.json", null, 0);
    expect(drawn.at(-1)?.lanes[0]?.catalog.name).toBe("Standard");
    expect(session.getSnapshot().lanes[0].catalogProblem).toContain("user:other.json");
  });

  it("starts lanes with the Personal default catalog when listed, and with the entry before's settings by position", async () => {
    const night = { id: "user:night.json", name: "Night", description: "" };
    const withNight = { ...(opened(10, TWO_LANES) as OpenedMessage), catalogs: [night] };
    const personal = await setup(undefined, { defaultCatalog: () => night.id });
    personal.server.say(withNight);
    expect(personal.session.laneSettings().map((lane) => lane.catalog)).toEqual([
      night.id,
      night.id,
    ]);

    const unlisted = await setup(undefined, { defaultCatalog: () => "user:gone.json" });
    unlisted.server.say(withNight);
    expect(unlisted.session.laneSettings().map((lane) => lane.catalog)).toEqual([
      "built-in:standard",
      "built-in:standard",
    ]);

    const seed = { ...LANE_SETTINGS, theme: "dusk", hidden: 2, tolerance_ms: null };
    const seeded = await setup(undefined, { defaultCatalog: () => night.id, lanes: [seed] });
    seeded.server.say(withNight);
    expect(seeded.session.laneSettings()).toEqual([seed, { ...LANE_SETTINGS, catalog: night.id }]);
    expect(policies(seeded.sent)[0]).toMatchObject({ lane: 0, policy: { tolerance_ms: null } });
  });

  it("gives the other lanes a lane's settings but its visibility, as one policy each", async () => {
    const { session, server, sent } = await setup();
    server.open(10, TWO_LANES);
    session.setLane(1, { visible: false });
    session.setLane(0, { sticky: { enabled: true, timeout_ms: 500, opacity: 0.5 }, theme: "dusk" });
    const before = policies(sent).length;
    session.applyToAll(0);
    expect(policies(sent).slice(before)).toMatchObject([
      { lane: 1, subscribed: false, policy: { sticky: { enabled: true, timeout_ms: 500 } } },
    ]);
    expect(session.laneSettings()[1]).toMatchObject({ theme: "dusk", visible: false });
    expect(session.getSnapshot().lanes[1].settings.sticky.opacity).toBe(0.5);
  });

  it("selects an object of the shown sample, asks the backend for its fields, and clears the selection", async () => {
    const { session, server, sent, drawn } = await shown();
    const columns = drawn.at(-1)?.lanes[0]?.columns as SampleView;
    const entity = columns.strings.string(columns.entityId[0]);
    const selection = { lane: 0, source: 0, entity };
    session.select(selection);
    expect(session.getSnapshot()).toMatchObject({
      selection,
      inspection: { state: "loading", summary: { entity } },
    });
    expect(sent.filter((message) => message.type === "Inspect")).toEqual([
      { type: "Inspect", source: 0, data_revision: 1, sample: 0, entity },
    ]);
    server.say({
      type: "Inspected",
      source: 0,
      data_revision: 1,
      sample: 0,
      entity,
      inspection: { state: "Found", fields: [], truncated: false },
    });
    expect(session.getSnapshot().inspection.state).toBe("ready");
    session.select({ ...selection, entity: "no such object" });
    expect(session.getSnapshot().inspection.state).toBe("absent");
    session.select(null);
    expect(session.getSnapshot()).toMatchObject({ selection: null, inspection: { state: "none" } });
  });

  it("asks about the selected object again only once the new connection holds its sample, whatever was refused before it", async () => {
    const { session, server, sent, drawn, retries, refresh } = await shown();
    const columns = drawn.at(-1)?.lanes[0]?.columns as SampleView;
    const entity = columns.strings.string(columns.entityId[0]);
    const inspected = (state: "Found" | "Unavailable") =>
      server.say({
        type: "Inspected",
        source: 0,
        data_revision: 1,
        sample: 0,
        entity,
        inspection:
          state === "Found" ? { state, fields: [], truncated: false } : { state: "Unavailable" },
      });
    const asks = () => sent.filter((message) => message.type === "Inspect").length;
    session.select({ lane: 0, source: 0, entity });
    inspected("Found");

    server.drop();
    retries[0].run();
    await flush();
    server.open(10, [DPM]);
    server.catalog();
    server.status(0, READY);
    // The new connection does not yet hold the sample, so nothing is asked of it.
    expect(asks()).toBe(1);
    inspected("Unavailable");
    server.frames(0, 1, 2, 3);
    refresh();
    server.window(0, 9);
    server.map({ policyRevision: 2, cells: [0, 0, NONE, EMPTY], kinds: [0, 2, 0, 0] });
    server.sample();
    refresh();
    expect(asks()).toBe(2);
    inspected("Found");
    expect(session.getSnapshot().inspection.state).toBe("ready");
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
    server.window(0, 9);
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

  it("tells how the lane matched a sample to the frame on screen", async () => {
    const { session, refresh } = await shown();
    expect(session.matched(0)).toEqual({ sample: 0, kind: 0, timeUs: 0, frame: 5 });
    session.step(1);
    refresh();
    expect(session.matched(0)).toMatchObject({ sample: 0, kind: 2 });
    session.step(1);
    refresh();
    expect(session.matched(0)).toEqual({ sample: null, kind: 0, timeUs: null, frame: null });
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
    // The viewer still holds the sample, as no Evict came: the new maps are all the lane waits for.
    server.map({ cells: [0, 0], policyRevision: 2 });
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0 });
    expect(session.getSnapshot().lanes[0].pending).toBe(false);
  });

  it("keeps the map cells inside the data window and the samples until the backend evicts them", async () => {
    const { session, server, drawn } = await shown();
    server.say({ type: "Evict", source: 0, data_revision: 7, samples: [0] });
    session.represent();
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0 });
    server.window(2, 3);
    session.represent();
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    server.window(0, 3);
    server.map({ cells: [0, 0, NONE, EMPTY] });
    session.represent();
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0 });
    server.say({ type: "Evict", source: 0, data_revision: 1, samples: [0] });
    session.represent();
    expect(drawn.at(-1)?.lanes).toEqual([undefined]);
    server.sample();
    expect(drawn.at(-1)?.lanes[0]).toMatchObject({ sample: 0 });
  });

  it("is never held up by a lane that is loading, failed, hidden or plain", async () => {
    const { session, server, presented, refresh } = await setup();
    server.open(10, [
      DPM,
      { label: "GT", slot: "GT", source: 1 },
      { label: "FRCNN", slot: "FRCNN", source: 2 },
      { label: "v", slot: "v", source: null },
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
