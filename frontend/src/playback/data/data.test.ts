// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { item } from "../fakeData";
import { MATCH_RETAINED } from "../protocol";
import { dataWindow } from "./dataWindow";
import { FrameMaps, UNKNOWN } from "./FrameMaps";
import { SampleStore } from "./SampleStore";

describe("sample store", () => {
  it("keeps items of the source's revision and drops those of another", () => {
    const store = new SampleStore();
    store.add(0, 1, 4, item());
    expect(store.get(0, 4)).toBeUndefined();
    store.setRevision(0, 1);
    store.add(0, 1, 4, item());
    store.add(0, 2, 5, item());
    expect(store.get(0, 4)?.rows).toBeGreaterThan(0);
    expect(store.get(0, 5)).toBeUndefined();
    expect(store.bytes).toBe(item().length);
    store.setRevision(0, 2);
    expect(store.get(0, 4)).toBeUndefined();
    expect(store.bytes).toBe(0);
  });

  it("evicts to the window only once the window is known, and then refuses what is outside it", () => {
    const store = new SampleStore();
    store.setRevision(0, 1);
    for (const sample of [1, 2, 3]) store.add(0, 1, sample, item());
    store.retain(0, null);
    expect([1, 2, 3].map((s) => store.get(0, s) !== undefined)).toEqual([true, true, true]);
    store.retain(0, new Set([2]));
    expect([1, 2, 3].map((s) => store.get(0, s) !== undefined)).toEqual([false, true, false]);
    store.add(0, 1, 3, item());
    expect(store.get(0, 3)).toBeUndefined();
    store.retain(0, null);
    store.add(0, 1, 3, item());
    expect(store.get(0, 3)).toBeDefined();
  });

  it("reports an item it cannot read and does not keep it", () => {
    const store = new SampleStore();
    store.setRevision(0, 1);
    expect(store.add(0, 1, 0, new Uint8Array(64))).toEqual(expect.any(String));
    expect(store.get(0, 0)).toBeUndefined();
  });
});

describe("frame maps", () => {
  const cells = Int32Array.from([3, 3, -1, -2]);
  const kinds = Uint8Array.from([0, 1, 0, MATCH_RETAINED]);

  it("holds the runs of the revisions a lane expects inside the window, in pages", () => {
    const maps = new FrameMaps(1000);
    maps.setRevisions(0, 1, 1);
    maps.retain(250, 520);
    expect(maps.add(0, 1, 1, 252, cells, kinds)).toBe(true);
    expect(maps.add(0, 1, 1, 256, cells, kinds)).toBe(true);
    expect(maps.add(0, 2, 1, 260, cells, kinds)).toBe(false);
    expect(maps.add(0, 1, 9, 260, cells, kinds)).toBe(false);
    expect([254, 255, 256, 259].map((f) => maps.at(0, f))).toEqual([-1, -2, 3, -2]);
    expect(maps.kind(0, 253)).toBe(1);
    expect(maps.at(0, 260)).toBe(UNKNOWN);
    expect(maps.at(1, 254)).toBe(UNKNOWN);
    expect(maps.add(0, 1, 1, 800, cells, kinds)).toBe(false);
  });

  it("forgets a lane's map when its revisions change, and frames the window leaves", () => {
    const maps = new FrameMaps(1000);
    maps.setRevisions(0, 1, 1);
    maps.retain(0, 100);
    maps.add(0, 1, 1, 10, cells, kinds);
    const out = new Int32Array(4);
    expect(maps.window(0, 10, out)).toBe(true);
    expect([...out]).toEqual([3, 3, -1, -2]);
    expect(maps.window(0, 9, out)).toBe(false);
    maps.retain(12, 100);
    expect([maps.at(0, 11), maps.at(0, 12)]).toEqual([UNKNOWN, -1]);
    maps.setRevisions(0, 2, 1);
    expect(maps.at(0, 12)).toBe(UNKNOWN);
  });
});

describe("data window", () => {
  const frames = { ahead: 4, behind: 5 };

  it("asks for nothing without a shown lane, and for the frame window when the items weigh nothing", () => {
    expect(dataWindow(null, 1000, frames)).toEqual({ data_ahead: 0, data_behind: 0 });
    expect(dataWindow(0, 1000, frames)).toEqual({ data_ahead: 4, data_behind: 5 });
  });

  it("fits a tenth of the budget, at least the playhead, split like the frame window", () => {
    expect(dataWindow(50, 1000, frames)).toEqual({ data_ahead: 0, data_behind: 1 });
    expect(dataWindow(30, 1000, frames)).toEqual({ data_ahead: 1, data_behind: 1 });
    expect(dataWindow(10_000, 1000, frames)).toEqual({ data_ahead: 0, data_behind: 0 });
    expect(dataWindow(1, 10 ** 9, { ahead: 5000, behind: 5000 })).toEqual({
      data_ahead: 1024,
      data_behind: 1023,
    });
  });
});
