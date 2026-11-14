// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { OPEN_RANGES } from "../present";
import { catalogDocument, item } from "../stream/fakeData";
import { LANE_DEFAULTS, LANE_SETTINGS } from "../lanes/fakeOpened";
import { LaneSet } from "../lanes/lanes";
import type { LaneStatusWire } from "../stream/protocol";
import { ViewerData } from "./viewerData";

const READY: LaneStatusWire = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 2024,
  ingest: { state: "Complete" },
};

/** One subscribed lane of source 0 whose catalog has arrived. */
function viewer() {
  const lanes = new LaneSet();
  lanes.open([{ label: "DPM", slot: "DPM", source: 0 }], LANE_SETTINGS);
  lanes.setStatus(0, READY);
  const data = new ViewerData(
    lanes,
    () => {},
    () => () => {},
  );
  data.open(10_000, 10 ** 9);
  data.refresh();
  data.catalogs.receive(LANE_DEFAULTS.catalog, 1, catalogDocument(), null);
  const map = (first: number, samples: number[]) =>
    data.addMap({
      kind: "map",
      lane: 0,
      policyRevision: 1,
      dataRevision: 1,
      first,
      samples: Int32Array.from(samples),
      kinds: new Uint8Array(samples.length),
    });
  const sample = (index: number) =>
    data.addSample({
      kind: "sample",
      source: 0,
      dataRevision: 1,
      sample: index,
      timeUs: null,
      frame: null,
      item: item(),
    });
  const state = (frame: number) => data.draw?.at(0, frame).state;
  return { lanes, data, map, sample, state };
}

describe("viewer data", () => {
  it("draws a sticky sample across a seek to a window that shares no frame with the old one", () => {
    const { data, map, sample, state } = viewer();
    data.window(0, 20);
    map(0, [0, 0, 0]);
    sample(0);
    expect(state(1)).toBe("draw");
    // The seek: the backend moves the window, the old cells go, and it sends no Evict for a sample the new
    // window still selects.
    data.window(9, 11);
    expect(state(1)).toBe("pending");
    expect(data.samples.get(0, 0)).toBeDefined();
    map(9, [0, 0, 0]);
    expect(state(10)).toBe("draw");
  });

  it("drops the map cells outside the data window and keeps the samples until they are evicted", () => {
    // A follower asks for far more than it is allowed: only the window the backend applied counts.
    const { data, map, sample, state } = viewer();
    data.window(100, 104);
    expect(
      map(
        90,
        Array.from({ length: 20 }, (_, k) => 90 + k),
      ),
    ).toBe(true);
    expect([95, 100, 104, 105].map(state)).toEqual(["pending", "pending", "pending", "pending"]);
    for (let k = 100; k <= 104; k++) sample(k);
    expect([95, 100, 104, 105].map(state)).toEqual(["pending", "draw", "draw", "pending"]);

    data.window(102, 104);
    expect([100, 102].map(state)).toEqual(["pending", "draw"]);
    expect(data.samples.get(0, 100)).toBeDefined();
    data.evict({ type: "Evict", source: 0, data_revision: 2, samples: [102] });
    expect(data.samples.get(0, 102)).toBeDefined();
    data.evict({ type: "Evict", source: 0, data_revision: 1, samples: [102] });
    expect(state(102)).toBe("pending");
    expect(data.samples.get(0, 102)).toBeUndefined();
  });

  it("keeps what it holds when a Ready repeats the revision while the file is read, and drops it for a new one", () => {
    const { lanes, data, map, sample, state } = viewer();
    data.window(0, 20);
    map(0, [0, 0, 0]);
    sample(0);
    lanes.setStatus(0, { ...READY, samples: 2000, ingest: { state: "Reading" } });
    data.refresh();
    expect(state(1)).toBe("draw");
    lanes.setStatus(0, { ...READY, data_revision: 2 });
    data.refresh();
    expect(state(1)).toBe("pending");
    expect(data.samples.count).toBe(0);
  });

  it("holds no sample of a source that failed, and takes up a new revision", () => {
    const { lanes, data, sample } = viewer();
    sample(0);
    lanes.setStatus(0, { status: "Loading" });
    data.refresh();
    expect(data.samples.get(0, 0)).toBeDefined();
    lanes.setStatus(0, { status: "Failed", code: "OverlayInvalid", params: {}, reason: "bad" });
    data.refresh();
    expect(data.samples.count).toBe(0);
    lanes.setStatus(0, { ...READY, data_revision: 2 });
    data.refresh();
    sample(1);
    expect(data.samples.count).toBe(0);
    data.addSample({
      kind: "sample",
      source: 0,
      dataRevision: 2,
      sample: 1,
      timeUs: null,
      frame: null,
      item: item(),
    });
    expect(data.samples.count).toBe(1);
  });

  it("counts the items of a source once however many lanes show it, so two lanes do not halve the data window", () => {
    const lanes = new LaneSet();
    lanes.open(
      [
        { label: "a", slot: "a", source: 0 },
        { label: "b", slot: "b", source: 0 },
        { label: "c", slot: "c", source: 1 },
      ],
      LANE_SETTINGS,
    );
    const data = new ViewerData(
      lanes,
      () => {},
      () => () => {},
    );
    expect(data.sampleBytes()).toBeNull();
    lanes.setStatus(0, READY);
    lanes.setStatus(1, READY);
    expect(data.sampleBytes()).toBe(2024);
    lanes.setStatus(2, { ...READY, mean_sample_bytes: 100 });
    expect(data.sampleBytes()).toBe(2124);
    lanes.set(0, { visible: false });
    expect(data.sampleBytes()).toBe(2124);
    lanes.set(1, { visible: false });
    expect(data.sampleBytes()).toBe(100);
  });

  it("draws one held sample in two lanes, each under its own map and look", () => {
    const lanes = new LaneSet();
    lanes.open(
      [
        { label: "a", slot: "a", source: 0 },
        { label: "b", slot: "b", source: 0 },
      ],
      LANE_SETTINGS,
    );
    lanes.setStatus(0, READY);
    lanes.setStatus(1, READY);
    const data = new ViewerData(
      lanes,
      () => {},
      () => () => {},
    );
    data.open(10, 10 ** 9);
    data.refresh();
    data.catalogs.receive(LANE_DEFAULTS.catalog, 1, catalogDocument(), null);
    data.window(0, 9);
    data.addSample({
      kind: "sample",
      source: 0,
      dataRevision: 1,
      sample: 0,
      timeUs: null,
      frame: null,
      item: item(),
    });
    const map = (lane: number, cells: number[]) =>
      data.addMap({
        kind: "map",
        lane,
        policyRevision: 1,
        dataRevision: 1,
        first: 0,
        samples: Int32Array.from(cells),
        kinds: new Uint8Array(cells.length),
      });
    map(0, [0, -1]);
    map(1, [0, 0]);
    lanes.set(1, { theme: "night", hidden: 1 });
    const at = (lane: number, frame: number) => data.draw?.at(lane, frame);
    expect([at(0, 0)?.state, at(1, 0)?.state]).toEqual(["draw", "draw"]);
    expect(at(0, 1)?.state).toBe("none");
    expect(at(1, 1)?.state).toBe("draw");
    const [first, second] = [at(0, 0), at(1, 0)];
    if (first?.state !== "draw" || second?.state !== "draw") throw new Error("not drawn");
    expect(first.frame.columns).toBe(second.frame.columns);
    expect([first.frame.theme, first.frame.hidden]).toEqual([null, 0]);
    expect([second.frame.theme, second.frame.hidden]).toEqual(["night", 1]);
    expect(second.frame.styleRevision).not.toBe(first.frame.styleRevision);
  });

  it("filters by a lane's score range as soon as it is set, not only after the next other change", () => {
    const { lanes, data, map, sample } = viewer();
    data.window(0, 9);
    map(0, [0]);
    sample(0);
    const filter = () => {
      const drawn = data.draw?.at(0, 0);
      if (drawn?.state !== "draw") throw new Error("not drawn");
      return drawn.frame.filter;
    };
    expect(filter()).toBeNull();
    lanes.set(0, { ranges: { ...OPEN_RANGES, score: { min: 0.5, max: null } } });
    expect(filter()).not.toBeNull();
  });

  it("applies a lane's expression only if it compiles, and never draws nothing for a catalog that failed", () => {
    const { lanes, data, map, sample } = viewer();
    data.window(0, 9);
    map(0, [0]);
    sample(0);
    const frame = () => {
      const drawn = data.draw?.at(0, 0);
      if (drawn?.state !== "draw") throw new Error("not drawn");
      return drawn.frame;
    };
    expect(frame().filter).toBeNull();
    lanes.set(0, { expression: "score >= 0.4" });
    const filter = frame().filter;
    expect(filter?.test).not.toBeNull();
    expect(frame().filter).toBe(filter);
    lanes.set(0, { expression: "score >=" });
    expect(frame().filter).toBeNull();
    lanes.set(0, { catalog: "user:broken.json" });
    data.refresh();
    data.receiveCatalogs({
      type: "Catalog",
      id: "user:broken.json",
      revision: 1,
      document: '{"schema": 4}',
      problem: null,
    });
    expect(data.draw?.at(0, 0).state).toBe("draw");
    expect(lanes.states[0].catalogProblem).toContain("/");
    expect(lanes.states[0].look).toBeDefined();
  });
});
