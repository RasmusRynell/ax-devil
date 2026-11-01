// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { LANE_DEFAULTS } from "./fakeOpened";
import { LaneSet } from "./lanes";

const ready = {
  status: "Ready" as const,
  clock: "frame" as const,
  filter: [],
  warnings: [],
  samples: 3,
};

function opened(): LaneSet {
  const lanes = new LaneSet();
  lanes.open(
    [
      { label: "DPM", has_overlay: true },
      { label: "plain", has_overlay: false },
    ],
    LANE_DEFAULTS,
  );
  return lanes;
}

describe("lanes", () => {
  it("starts overlay lanes loading and an entry without overlays plain", () => {
    expect(opened().states.map((lane) => lane.status)).toEqual([{ status: "Loading" }, "plain"]);
  });

  it("awaits a lane's overlay only once it is ready and shown", () => {
    const lanes = opened();
    expect(lanes.awaited(0)).toBe(false);
    lanes.setStatus(0, ready);
    lanes.setStatus(1, ready);
    expect([lanes.awaited(0), lanes.awaited(1)]).toEqual([true, false]);
    lanes.setView(0, { visible: false });
    expect(lanes.awaited(0)).toBe(false);
    lanes.setStatus(0, { status: "Failed", reason: "bad", location: "x" });
    lanes.setView(0, { visible: true });
    expect(lanes.awaited(0)).toBe(false);
  });

  it("numbers each real change of a view and clears the problem it follows", () => {
    const lanes = opened();
    expect(lanes.setView(0, { catalog: "user:tracking" })).toMatchObject({
      type: "LaneView",
      lane: 0,
      revision: 1,
    });
    lanes.setProblem(0, "no such catalog");
    expect(lanes.states[0].problem).toBe("no such catalog");
    expect(lanes.setView(0, { catalog: "user:tracking" })).toBeUndefined();
    expect(lanes.setView(0, { filter: { show_car: false } })).toMatchObject({ revision: 2 });
    expect(lanes.states[0].problem).toBeUndefined();
  });

  it("retargets only lanes whose displayed size changed, in whole pixels", () => {
    const lanes = opened();
    lanes.setTargets([
      { width: 640.4, height: 360.6 },
      { width: 0, height: 0 },
    ]);
    expect(lanes.retarget()).toEqual([
      expect.objectContaining({
        lane: 0,
        revision: 1,
        view: expect.objectContaining({ target: { width: 640, height: 361 } }),
      }),
    ]);
    expect(lanes.retarget()).toEqual([]);
  });

  it("keeps the user's views when the same lanes open again, and returns them to send", () => {
    const lanes = opened();
    lanes.setView(0, { visible: false });
    const resend = lanes.open(
      [
        { label: "DPM", has_overlay: true },
        { label: "plain", has_overlay: false },
      ],
      LANE_DEFAULTS,
    );
    expect(resend).toEqual([
      expect.objectContaining({
        lane: 0,
        revision: 1,
        view: expect.objectContaining({ visible: false }),
      }),
    ]);
    expect(lanes.states[0].status).toEqual({ status: "Loading" });
    lanes.open([{ label: "other", has_overlay: true }], LANE_DEFAULTS);
    expect(lanes.states[0]).toMatchObject({ revision: 0, view: LANE_DEFAULTS });
  });
});
