import { PerformanceObserver, constants } from "node:perf_hooks";
import { getHeapStatistics } from "node:v8";
import { builtIn } from "./canonical/render";
import { evaluate } from "./evaluate/evaluate";
import { EvalReport } from "./evaluate/report";
import { RowSet, filterRows } from "./filter/filter";
import { syntheticSample } from "./fixtures";
import { Layouter } from "./layout/layout";
import { MarkBuffer } from "./marks/buffer";
import { LaneQuads } from "./quads/lane";
import { QuadSink } from "./quads/quadSink";
import { FakeText } from "./text/fakeSource";

const FRAMES = Number(process.env.AX_BENCH_FRAMES ?? 300);
const WARMUP = 60;
const LANES = 4;
const VIEW = { width: 1280, height: 720, dpr: 1, zoom: 1 };

const percentile = (sorted: number[], p: number): number =>
  sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];

interface Lane {
  rows: RowSet;
  marks: MarkBuffer;
  report: EvalReport;
  quads: LaneQuads;
  sink: QuadSink;
  layouter: Layouter;
}

/**
 * "warm": every lane shows its own sample frame after frame, glyphs known to the atlas. "cold": every lane shows a
 * sample it has not shown for several frames and the atlas page starts empty every frame. "hit": warm, through
 * `LanePresenter`, whose cache the unchanged input hits.
 */
export type Mode = "warm" | "cold" | "hit";

/** One benchmark row: milliseconds per lane frame and per four-lane frame, memory and collector figures. */
export interface BenchResult {
  catalog: string;
  count: number;
  mode: Mode;
  lane: { p50: number; p95: number };
  /** Median share of a lane frame in filter and evaluate, and in layout. */
  evalP50: number;
  layoutP50: number;
  lanes4: { p50: number; p95: number };
  quadBytesPerObject: number;
  garbageKbPerObject: number;
  minorGcs: number;
  minorGcMs: number;
  retainedKb: number;
}

/** Run `FRAMES` frames of four lanes of `count` objects with the built-in catalog `name`. */
export async function scenario(name: string, count: number, mode: Mode): Promise<BenchResult> {
  const cold = mode === "cold";
  const catalog = builtIn(name);
  const samples = Array.from({ length: cold ? 2 * LANES : LANES }, (_, v) =>
    syntheticSample(count, v),
  );
  const text = new FakeText();
  const lanes: Lane[] = Array.from({ length: LANES }, () => {
    const quads = new LaneQuads();
    return {
      rows: new RowSet(),
      marks: new MarkBuffer(),
      report: new EvalReport(),
      quads,
      sink: new QuadSink(quads),
      layouter: new Layouter(text),
    };
  });
  const mask = { enabled: new Uint32Array(samples[0].maskWords), options: 0 };
  const style = { hidden: 0, theme: null };
  const { LanePresenter } = mode === "hit" ? await import("./lane") : { LanePresenter: null };
  const presenters = LanePresenter ? lanes.map(() => new LanePresenter(text)) : [];
  const frame = (
    f: number,
    times: { e: number[]; l: number[]; t: number[]; all: number[] } | null,
  ): void => {
    if (cold) text.reset();
    const start = performance.now();
    lanes.forEach((lane, i) => {
      const sample = samples[(cold ? f * LANES + i : i) % samples.length];
      const t0 = performance.now();
      if (presenters.length > 0) {
        presenters[i].present({ catalog, sample, style, mask, filter: null }, VIEW);
        times?.t.push(performance.now() - t0);
        return;
      }
      filterRows(sample, null, mask, lane.rows);
      evaluate(sample, lane.rows, catalog, style, lane.marks, lane.report);
      const t1 = performance.now();
      lane.layouter.run(lane.marks, catalog, sample, VIEW, lane.sink, lane.report);
      const t2 = performance.now();
      times?.e.push(t1 - t0);
      times?.l.push(t2 - t1);
      times?.t.push(t2 - t0);
    });
    times?.all.push(performance.now() - start);
  };
  for (let f = 0; f < WARMUP; f++) frame(f, null);
  let pause = 0;
  let minors = 0;
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      const kind = (entry as unknown as { detail: { kind: number } }).detail.kind;
      if (kind === constants.NODE_PERFORMANCE_GC_MINOR) {
        pause += entry.duration;
        minors++;
      }
    }
  });
  observer.observe({ entryTypes: ["gc"] });
  globalThis.gc?.();
  const before = getHeapStatistics().used_heap_size;
  const times = { e: [] as number[], l: [] as number[], t: [] as number[], all: [] as number[] };
  for (let f = 0; f < FRAMES; f++) frame(f, times);
  await new Promise((resolve) => setTimeout(resolve, 20));
  observer.disconnect();
  globalThis.gc?.();
  const retained = getHeapStatistics().used_heap_size - before;
  // Garbage of one four-lane frame: the largest heap growth over a few frames that each start on a collected heap. A
  // scavenge inside the frame hides some of it, so start node with --min-semi-space-size=128 for the large sizes.
  let garbage = 0;
  for (let f = 0; f < 5; f++) {
    globalThis.gc?.();
    const start = getHeapStatistics().used_heap_size;
    frame(f, null);
    garbage = Math.max(garbage, getHeapStatistics().used_heap_size - start);
  }
  for (const key of ["e", "l", "t", "all"] as const) times[key].sort((a, b) => a - b);
  const quadCount =
    presenters.length > 0 ? presenters[0].quads.quads.count : lanes[0].quads.quads.count;
  return {
    catalog: name,
    count,
    mode,
    lane: { p50: percentile(times.t, 0.5), p95: percentile(times.t, 0.95) },
    evalP50: percentile(times.e, 0.5) || 0,
    layoutP50: percentile(times.l, 0.5) || 0,
    lanes4: { p50: percentile(times.all, 0.5), p95: percentile(times.all, 0.95) },
    quadBytesPerObject: (quadCount * 48) / count,
    garbageKbPerObject: garbage / 1024 / LANES / count,
    minorGcs: minors,
    minorGcMs: pause,
    retainedKb: retained / 1024,
  };
}
