// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { LANE_DEFAULTS } from "./fakeOpened";
import { LaneSet, startWith } from "./lanes";
import type { LaneStatusWire } from "./protocol";

const ready: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [],
  warnings: [],
  samples: 3,
  data_revision: 1,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};
const LANES = [
  { label: "DPM", source: 0 },
  { label: "plain", source: null },
];

function opened(): LaneSet {
  const lanes = new LaneSet();
  lanes.open(LANES, LANE_DEFAULTS);
  return lanes;
}

describe("lanes", () => {
  it("starts source lanes loading and a lane without a source plain", () => {
    expect(opened().states.map((lane) => lane.status)).toEqual([{ status: "Loading" }, "plain"]);
  });

  it("draws a lane only once it is ready and shown", () => {
    const lanes = opened();
    expect(lanes.awaited(0)).toBeUndefined();
    lanes.setStatus(0, ready);
    lanes.setStatus(1, ready);
    expect([lanes.awaited(0), lanes.awaited(1)]).toEqual([ready, undefined]);
    lanes.set(0, { visible: false });
    expect(lanes.awaited(0)).toBeUndefined();
    lanes.set(0, { visible: true });
    lanes.setStatus(0, { status: "Failed", code: "OverlayInvalid", params: {}, reason: "bad" });
    expect(lanes.awaited(0)).toBeUndefined();
  });

  it("numbers a change the backend selects by, and not one that is only about looks", () => {
    const lanes = opened();
    expect(lanes.set(0, { tolerance_ms: 100 })).toMatchObject({
      type: "LanePolicy",
      lane: 0,
      revision: 2,
      policy: { tolerance_ms: 100 },
      subscribed: true,
    });
    lanes.setProblem(0, "bad timeout");
    expect(lanes.set(0, { tolerance_ms: 100 })).toBeUndefined();
    expect(lanes.set(0, { catalog: "built-in:classic" })).toBeUndefined();
    expect(
      lanes.set(0, { sticky: { enabled: false, timeout_ms: 2050, opacity: 0.5 } }),
    ).toBeUndefined();
    expect(lanes.states[0]).toMatchObject({ policyRevision: 2, problem: "bad timeout" });
    expect(lanes.styleRevision(0)).toBe(1);
    expect(lanes.set(0, { visible: false })).toMatchObject({ revision: 3, subscribed: false });
    expect(lanes.states[0].problem).toBeUndefined();
  });

  it("subscribes every source lane when the entry opens, keeping the user's settings across a reconnection", () => {
    const lanes = new LaneSet();
    expect(lanes.open(LANES, LANE_DEFAULTS)).toMatchObject([
      { lane: 0, revision: 1, subscribed: true },
    ]);
    lanes.set(0, { visible: false });
    expect(lanes.open(LANES, LANE_DEFAULTS)).toMatchObject([
      { lane: 0, revision: 3, subscribed: false },
    ]);
    expect(lanes.states[0].status).toEqual({ status: "Loading" });
    lanes.open([{ label: "other", source: 0 }], LANE_DEFAULTS);
    expect(lanes.states[0]).toMatchObject({ policyRevision: 1, settings: { visible: true } });
  });

  it("treats theme, hidden features and the expression as looks: a new style, no policy", () => {
    const lanes = opened();
    for (const change of [{ theme: "night" }, { hidden: 0b101 }, { expression: "score > 0.4" }]) {
      const before = lanes.styleRevision(0);
      expect(lanes.set(0, change)).toBeUndefined();
      expect(lanes.styleRevision(0)).toBe(before + 1);
      expect(lanes.set(0, change)).toBeUndefined();
      expect(lanes.styleRevision(0)).toBe(before + 1);
    }
    expect(lanes.states[0].policyRevision).toBe(1);
  });

  it("starts new lanes from the seeds by position, else the defaults, and a reconnection from what the user set", () => {
    const seeds = [{ ...opened().states[0].settings, theme: "night", visible: false }];
    const lanes = new LaneSet();
    lanes.open(LANES, LANE_DEFAULTS, seeds);
    expect(lanes.states.map((lane) => lane.settings.theme)).toEqual(["night", null]);
    expect(lanes.states[0].settings.visible).toBe(false);
    lanes.set(0, { theme: "day" });
    lanes.open(LANES, LANE_DEFAULTS, seeds);
    expect(lanes.states[0].settings.theme).toBe("day");
  });

  it("copies a lane's settings but its visibility to the other lanes with a source", () => {
    const lanes = new LaneSet();
    lanes.open(
      [
        { label: "a", source: 0 },
        { label: "b", source: 1 },
        { label: "v", source: null },
        { label: "c", source: 2 },
      ],
      LANE_DEFAULTS,
    );
    lanes.set(1, { visible: false });
    lanes.set(0, {
      catalog: "user:a.json",
      theme: "night",
      expression: "score > 0.4",
      filter: { car: false },
      tolerance_ms: null,
    });
    const messages = lanes.copyFrom(0);
    expect(messages).toMatchObject([
      { lane: 1, revision: 3, subscribed: false, policy: { tolerance_ms: null } },
      { lane: 3, revision: 2, subscribed: true, policy: { tolerance_ms: null } },
    ]);
    const [a, b, plain, c] = lanes.states.map((lane) => lane.settings);
    expect(b).toEqual({ ...a, visible: false });
    expect(c).toEqual(a);
    expect(plain.catalog).toBe(LANE_DEFAULTS.catalog);
    expect(lanes.copyFrom(7)).toEqual([]);
  });

  it("starts with the Personal default catalog only when the backend lists it", () => {
    const listed = [{ id: "user:a.json", name: "A", description: "" }];
    expect(startWith(LANE_DEFAULTS, listed, "user:a.json").catalog).toBe("user:a.json");
    expect(startWith(LANE_DEFAULTS, listed, "user:gone.json")).toBe(LANE_DEFAULTS);
    expect(startWith(LANE_DEFAULTS, listed, "")).toBe(LANE_DEFAULTS);
    expect(startWith(LANE_DEFAULTS, listed, null)).toBe(LANE_DEFAULTS);
  });
});
