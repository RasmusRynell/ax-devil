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

const FRAMES = 300;
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

/** Run `FRAMES` frames of four lanes of `count` objects with the built-in catalog `name`; one result line. */
export async function scenario(name: string, count: number, cold: boolean): Promise<string> {
  const catalog = builtIn(name);
  const variants = cold ? [0, 1, 2, 3] : [0];
  const samples = variants.map((v) => syntheticSample(count, v));
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
  const frame = (
    f: number,
    times: { e: number[]; l: number[]; t: number[]; all: number[] } | null,
  ): void => {
    const sample = samples[f % samples.length];
    if (cold) text.reset();
    const start = performance.now();
    for (const lane of lanes) {
      const t0 = performance.now();
      filterRows(sample, null, mask, lane.rows);
      evaluate(sample, lane.rows, catalog, style, lane.marks, lane.report);
      const t1 = performance.now();
      lane.layouter.run(lane.marks, catalog, sample, VIEW, lane.sink, lane.report);
      const t2 = performance.now();
      times?.e.push(t1 - t0);
      times?.l.push(t2 - t1);
      times?.t.push(t2 - t0);
    }
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
  const ms = (list: number[], p: number) => percentile(list, p).toFixed(3).padStart(7);
  const lane = lanes[0];
  const quadBytes = ((lane.quads.quads.count * 48) / count).toFixed(0);
  return [
    `${name.padEnd(8)} ${String(count).padStart(5)} ${cold ? "cold" : "warm"}`,
    `eval ${ms(times.e, 0.5)} ${ms(times.e, 0.95)}`,
    `layout ${ms(times.l, 0.5)} ${ms(times.l, 0.95)}`,
    `lane ${ms(times.t, 0.5)} ${ms(times.t, 0.95)}`,
    `4 lanes ${ms(times.all, 0.5)} ${ms(times.all, 0.95)}`,
    `garbage ${(garbage / 1024 / LANES / count).toFixed(2)} KB/object`,
    `minor GC ${minors} ${pause.toFixed(1)} ms`,
    `retained ${(retained / 1024).toFixed(0)} KB`,
    `${quadBytes} quad B/object`,
  ].join(" | ");
}
