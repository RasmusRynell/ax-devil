// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { builtIn } from "./canonical/render";
import { syntheticSample } from "./fixtures";
import { type LaneInput, LanePresenter } from "./lane";
import { FakeText } from "./text/fakeSource";

// The cache rules of a lane (spec 5.2): what makes it evaluate again, what makes it lay out again, and what neither.
describe("lane presenter", () => {
  const sample = syntheticSample(50);
  const input = (): LaneInput => ({
    catalog: builtIn("standard"),
    sample,
    style: { hidden: 0, theme: null },
    mask: { enabled: new Uint32Array(sample.maskWords), options: 0 },
    filter: null,
  });
  const view = { width: 1280, height: 720, dpr: 1, zoom: 1 };

  it("repeats nothing for the same data and view, and evaluates only when the data or style changed", () => {
    const lane = new LanePresenter(new FakeText());
    const base = input();
    const first = lane.present(base, view).quads.take();
    lane.present(base, { ...view });
    expect([lane.evaluations, lane.layouts]).toEqual([1, 1]);

    lane.present(base, { ...view, width: 1000 });
    expect([lane.evaluations, lane.layouts]).toEqual([1, 2]);

    base.style = { hidden: 1, theme: null };
    lane.present(base, { ...view, width: 1000 });
    expect([lane.evaluations, lane.layouts]).toEqual([2, 3]);

    base.style = { hidden: 0, theme: null };
    expect(lane.present(base, view).quads.take()).toEqual(first);
  });

  it("evaluates again when the class options change", () => {
    const lane = new LanePresenter(new FakeText());
    const base = input();
    lane.present(base, view);
    lane.present({ ...base, mask: { enabled: base.mask.enabled, options: 1 } }, view);
    expect(lane.evaluations).toBe(2);
  });

  it("lays out again when the atlas page was reset", () => {
    const text = new FakeText();
    const lane = new LanePresenter(text);
    lane.present(input(), view);
    text.reset();
    lane.present(input(), view);
    expect(lane.layouts).toBe(2);
  });

  it("keeps a layout for a small pan of a zoomed view and makes a new one for a big pan", () => {
    const lane = new LanePresenter(new FakeText());
    const zoomed = { width: 5120, height: 2880, dpr: 1, zoom: 1 };
    const window = (left: number) => ({ left, top: 1000, right: left + 1280, bottom: 1720 });
    const base = input();
    lane.present(base, { ...zoomed, visible: window(1920) });
    lane.present(base, { ...zoomed, visible: window(2200) });
    expect(lane.layouts).toBe(1);
    lane.present(base, { ...zoomed, visible: window(3800) });
    expect(lane.layouts).toBe(2);
  });
});
