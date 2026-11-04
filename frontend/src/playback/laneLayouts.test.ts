// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { builtIn } from "../present/canonical/render";
import { fullSample, syntheticSample } from "../present/fixtures";
import { AtlasFull, compileFilter, type FontHandle, type Glyph } from "../present";
import type { CompiledFilter, SampleView } from "../present";
import { FakeText } from "../present/text/fakeSource";
import type { LaneFrame } from "./contract";
import { describeRow, LaneLayouts, type PlacedLane } from "./laneLayouts";

// No page here: the shell's bridge is a field of a stand-in window.
const page: { axDevilWindow?: { zoomFactor: () => number } } = {};
beforeEach(() => vi.stubGlobal("window", page));
afterEach(() => {
  delete page.axDevilWindow;
  vi.unstubAllGlobals();
});

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

const frameOf = (sample: SampleView, overrides: Partial<LaneFrame> = {}): LaneFrame => ({
  source: 0,
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
  ...overrides,
});

const lane = (index: number, variant: number): PlacedLane => ({
  lane: index,
  frame: frameOf(syntheticSample(20, variant), { source: index }),
  view: { width: 1280, height: 720, dpr: 1 },
});

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

  it("lays out again when the page zoom changes, so px lengths keep their size on screen", () => {
    const layouts = new LaneLayouts(new FakeText());
    const placed = lane(0, 0);
    const version = () => layouts.layOut([placed])[0]?.version;
    expect(version()).toBe(1);
    expect(version()).toBe(1);
    page.axDevilWindow = { zoomFactor: () => 2 };
    expect(version()).toBe(2);
    expect(version()).toBe(2);
  });

  describe("objects", () => {
    const sample = fullSample([
      { id: "t7", class: "car", score: 0.8234, confidence: 0.9, box: [0.1, 0.1, 0.5, 0.5] },
      { id: "t9", box: [0.6, 0.6, 0.9, 0.9] },
    ]);
    const place = (overrides: Partial<LaneFrame> = {}): PlacedLane => ({
      lane: 0,
      frame: frameOf(sample, overrides),
      view: { width: 1000, height: 500, dpr: 1 },
    });
    const filterOut = (expression: string): CompiledFilter => {
      const result = compileFilter(expression);
      if (!result.ok) throw new Error("the filter does not compile");
      return result.filter;
    };

    it("picks and finds a row of the lane's last sample, also with every feature hidden", () => {
      const layouts = new LaneLayouts(new FakeText());
      layouts.layOut([place({ hidden: 0xffff })]);
      expect(layouts.pick(0, 0.3, 0.3)?.row).toBe(0);
      expect(layouts.pick(0, 0.55, 0.3)).toBeNull();
      expect(layouts.find(0, "t9")?.row).toBe(1);
      expect(layouts.find(0, "t1")).toBeNull();
      expect(layouts.pick(1, 0.3, 0.3)).toBeNull();
    });

    it("does not pick or find what the filter dropped", () => {
      const layouts = new LaneLayouts(new FakeText());
      layouts.layOut([place({ filter: filterOut("score > 0.5") })]);
      expect(layouts.pick(0, 0.3, 0.3)?.row).toBe(0);
      expect(layouts.find(0, "t9")).toBeNull();
      layouts.layOut([place({ filter: filterOut("score > 2") })]);
      expect(layouts.pick(0, 0.3, 0.3)).toBeNull();
      expect(layouts.find(0, "t7")).toBeNull();
    });

    it("describes a row as the id and class, then the facts the decoder gave, as the reference does", () => {
      expect(describeRow(sample, 0)).toMatchObject({
        entity: "t7",
        label: "t7 car",
        detail: "score 0.82 · confidence 0.90",
        category: 5,
      });
      expect(describeRow(sample, 1)).toMatchObject({
        label: "t9 unknown",
        detail: "",
        category: 7,
      });
    });

    it("says speed after the other facts and rounds ties to even, as the reference prints them", () => {
      const rows = fullSample([
        { id: "a", score: 0.125, confidence: 0.9, speed: 0.25 },
        { id: "b", speed: 2.5 },
      ]);
      expect(describeRow(rows, 0).detail).toBe("score 0.12 · confidence 0.90 · speed 0.2");
      expect(describeRow(rows, 1).detail).toBe("speed 2.5");
    });
  });
});
