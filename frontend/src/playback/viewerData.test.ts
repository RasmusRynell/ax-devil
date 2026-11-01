// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { item } from "./fakeData";
import { LANE_DEFAULTS } from "./fakeOpened";
import { LaneSet } from "./lanes";
import type { LaneStatusWire, Want } from "./protocol";
import { ViewerData } from "./viewerData";

const READY: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  mean_sample_bytes: 2024,
};

describe("viewer data", () => {
  it("keeps what a follower's maps select however little of its window the backend may send", () => {
    // The follower asks for 100 frames either side of the presenter but is authorized for four, so the maps of
    // the rest never come and the window is never complete.
    const lanes = new LaneSet();
    lanes.open([{ label: "DPM", source: 0 }], LANE_DEFAULTS);
    lanes.setStatus(0, READY);
    const data = new ViewerData(lanes, () => {});
    data.open(10_000, 10 ** 10);
    data.sync();
    const want = (playhead: number): Want => ({
      playhead,
      ahead: 100,
      behind: 100,
      direction: "forward",
      data_ahead: 100,
      data_behind: 100,
    });
    for (let playhead = 100; playhead < 400; playhead++) {
      data.retain(want(playhead));
      const first = playhead - 4;
      // The nine frames around the presenter: one sample each, the sample being the frame.
      data.addMap({
        kind: "map",
        lane: 0,
        policyRevision: 1,
        dataRevision: 1,
        first,
        samples: Int32Array.from({ length: 9 }, (_, k) => first + k),
        kinds: new Uint8Array(9),
      });
      for (let k = 0; k < 9; k++)
        data.addSample({
          kind: "sample",
          source: 0,
          dataRevision: 1,
          sample: first + k,
          item: item(),
        });
      data.retain(want(playhead));
    }
    // What is held follows the requested window (201 frames), not the 300 frames the presenter has been through.
    expect(data.samples.bytes).toBeLessThanOrEqual(201 * item().length);
    expect(data.samples.get(0, 395)).toBeDefined();
    expect(data.samples.get(0, 330)).toBeDefined();
    expect(data.samples.get(0, 120)).toBeUndefined();
  });
});
