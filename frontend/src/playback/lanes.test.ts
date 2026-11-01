// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { LANE_DEFAULTS } from "./fakeOpened";
import { LaneSet } from "./lanes";
import type { LaneStatusWire } from "./protocol";

const ready: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [],
  warnings: [],
  samples: 3,
  data_revision: 1,
  mean_sample_bytes: 100,
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
});
