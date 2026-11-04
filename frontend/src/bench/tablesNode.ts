// Benchmark of what Frame scope's object list computes in plain Node, no test runner:
//   make bench-tables
//   AX_BENCH_SIZES=5000 AX_BENCH_RUNS=300 make bench-tables
// The panel lists the objects of the frame on screen: `frameObjects`, a search over them and a sort run for every
// drawn frame; the table then measures and fits its columns for the rows in view. This times each of those steps and
// the whole per-frame pipeline over the objects of `syntheticSample`, p50 and p95 in milliseconds. Frame scope's budget
// is 2 ms p95 at 5000 objects. Layout, painting and React are not here: `BENCH_SUITE=tables make bench-browser`.
// Node reads the sources through the loader of `bench/node.ts`, which the Makefile target preloads.
import { frameObjects, recordOf, sortObjects, type FrameObject, type Sort } from "../tools/objects";
import type { Column } from "../tools/table/columns";
import { fitColumns } from "../tools/table/fit";
import { measureColumns, type Fonts } from "../tools/table/measure";
import type { LaneFrame } from "../playback/contract";
import { syntheticSample } from "../present/fixtures";
import { matches } from "../search/match";
import { parseSearch } from "../search/query";
import { summarize } from "./stats";
import type { Stat } from "./types";

const sizes = (process.env.AX_BENCH_SIZES ?? "1000,5000,10000,50000").split(",").map(Number);
const runs = Number(process.env.AX_BENCH_RUNS ?? 200);
const ROWS = 60;
/** What the per-frame pipeline may cost at this many objects. */
const BUDGET = { count: 5000, p95: 2 };

/** A canvas whose text is as wide as it is long, so `measureText` takes its cached path as in the app. */
class FakeCanvas {
  getContext() {
    return { font: "", measureText: (text: string) => ({ width: text.length * 7 }) };
  }
}
Object.assign(globalThis, { OffscreenCanvas: FakeCanvas });

const FONTS: Fonts = {
  body: { css: "14px sans-serif", ch: 7 },
  mono: { css: "13px monospace", ch: 8 },
  small: { css: "12px sans-serif", ch: 6 },
  pad: 12,
};

const score = (value: number | null): string =>
  value === null ? "" : String(Number(value.toFixed(2)));
/** The columns of `ObjectsPanel`'s Frame scope; the panel is a `.tsx` that Node cannot load. */
const COLUMNS: readonly Column<FrameObject>[] = [
  { key: "id", title: "ID", kind: "id", text: (o) => o.id, sortable: true },
  { key: "class", title: "Class", kind: "text", text: (o) => o.className, stretch: true },
  { key: "score", title: "Score", kind: "number", text: (o) => score(o.score) },
  { key: "confidence", title: "Conf", kind: "number", text: (o) => score(o.confidence) },
  { key: "motion", title: "Motion", kind: "text", text: (o) => o.motion, priority: 1 },
];

/** Milliseconds of `runs` calls of `work` after a warm-up. */
function time(work: () => unknown): Stat {
  for (let i = 0; i < 10; i++) work();
  const samples: number[] = [];
  for (let i = 0; i < runs; i++) {
    const start = performance.now();
    work();
    samples.push(performance.now() - start);
  }
  return summarize(samples);
}

const SORTS: Sort[] = [
  { key: "score", descending: true },
  { key: "confidence", descending: true },
  { key: "id", descending: false },
  { key: "class", descending: false },
  { key: "motion", descending: false },
];

/** One line of the table; `budgeted` lines are held to the Frame scope budget at its size. */
const line = (label: string, count: number, { p50, p95 }: Stat, budgeted: boolean): string => {
  const over = budgeted && count === BUDGET.count && p95 > BUDGET.p95;
  return `${label.padEnd(34)} ${String(count).padStart(6)} ${p50.toFixed(3).padStart(9)} ${p95.toFixed(3).padStart(9)}${over ? `  over ${BUDGET.p95} ms` : ""}`;
};

process.stdout.write(
  `${"step".padEnd(34)} ${"objects".padStart(6)} ${"p50 ms".padStart(9)} ${"p95 ms".padStart(9)}\n`,
);
for (const count of sizes) {
  const columns = syntheticSample(count);
  const frame = {
    columns,
    classMask: { enabled: new Uint32Array(columns.maskWords), options: 0 },
    filter: null,
  } as unknown as LaneFrame;
  const objects = frameObjects(frame);
  const out: string[] = [];
  const add = (label: string, work: () => unknown, budgeted = false) =>
    out.push(line(label, count, time(work), budgeted));

  add("frameObjects", () => frameObjects(frame));
  for (const query of ["", "car", "id:track-0 OR score>0.5"]) {
    const { wire } = parseSearch(query);
    add(`search "${query}"`, () => objects.filter((object) => matches(wire, recordOf(object))));
  }
  for (const sort of SORTS)
    add(`sort ${sort.key}${sort.descending ? " desc" : ""}`, () => sortObjects(objects, sort));

  // What the panel does for every drawn frame in Frame scope, with the default sort.
  const { wire } = parseSearch("");
  add(
    "frame: objects+search+sort",
    () =>
      sortObjects(
        frameObjects(frame).filter((object) => matches(wire, recordOf(object))),
        SORTS[0],
      ),
    true,
  );
  const sorted = sortObjects(objects, SORTS[0]);
  const inView = sorted.slice(0, ROWS);
  const measures = measureColumns(COLUMNS, inView, FONTS);
  add(`measureColumns ${ROWS} rows`, () => measureColumns(COLUMNS, inView, FONTS));
  for (const available of [260, 420, 640])
    add(`fitColumns ${available}px`, () => fitColumns(COLUMNS, measures, available, new Map()));
  add(
    "frame: view measure+fit",
    () => fitColumns(COLUMNS, measureColumns(COLUMNS, inView, FONTS), 420, new Map()),
    true,
  );
  process.stdout.write(`${out.join("\n")}\n`);
}
