// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { builtIn } from "../present/canonical/render";
import { syntheticSample } from "../present/fixtures";
import { AtlasFull, type FontHandle, type Glyph } from "../present";
import { FakeText } from "../present/text/fakeSource";
import type { LaneFrame } from "./contract";
import { LaneLayouts, type PlacedLane } from "./laneLayouts";

/** A page that is full after `room` glyph lookups, until reset. */
class SmallPage extends FakeText {
  lookups = 0;

  constructor(private readonly room: number) {
    super();
  }

  glyph(font: FontHandle, codePoint: number): Glyph {
    if (!this.dropOverflow && this.lookups++ >= this.room) throw new AtlasFull();
    return super.glyph(font, codePoint);
  }
}

const lane = (index: number, variant: number): PlacedLane => {
  const sample = syntheticSample(20, variant);
  const frame: LaneFrame = {
    source: index,
    dataRevision: 1,
    sample: 0,
    columns: sample,
    catalog: builtIn("standard"),
    styleRevision: 0,
    classMask: { enabled: new Uint32Array(sample.maskWords), options: 0 },
    filter: null,
    hidden: 0,
    theme: null,
    opacity: 1,
  };
  return { lane: index, frame, view: { width: 1280, height: 720, dpr: 1 } };
};

describe("lane layouts", () => {
  it("lays out an earlier lane again when a later one starts a new atlas page", () => {
    const first = lane(0, 0);
    const second = lane(1, 1);
    // Room for the first lane and not for the second.
    const measure = new SmallPage(Infinity);
    new LaneLayouts(measure).layOut([first]);

    const text = new SmallPage(measure.lookups);
    const layouts = new LaneLayouts(text);
    const drawings = layouts.layOut([first, second]);
    expect(text.generation).toBe(1);
    // The first lane was laid out twice (before and after the reset), the second once.
    expect(drawings.map((drawing) => drawing?.version)).toEqual([2, 1]);
    expect(layouts.layOut([first, second]).map((drawing) => drawing?.version)).toEqual([2, 1]);
  });
});
