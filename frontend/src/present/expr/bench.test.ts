// @vitest-environment node
// Micro-benchmark of compiled expressions and whole catalogs, skipped in the normal suite:
//   AX_BENCH=1 pnpm --dir frontend exec vp test run src/present/expr/bench.test.ts
// Prints nanoseconds per row (median of passes over 10,000 rows) and the V8 heap growth per row, and asserts nothing
// about speed.
import { readFileSync } from "node:fs";
import { getHeapStatistics } from "node:v8";
import { describe, it } from "vite-plus/test";
import { compileCatalog } from "../catalog/compile";
import { computeVars } from "../catalog/program";
import type { CompiledCatalog } from "../catalog/types";
import { ENTITY } from "./check";
import { CodePoints } from "./codePoints";
import type { Scalar } from "./compiled";
import { compileExpression } from "./compile";
import { type FixtureEntity, makeSample } from "./sampleFixture";

const ROWS = 10_000;
const CLASSES = ["car", "human", "head", "bus", "dog"];
const SAMPLE = makeSample(
  Array.from({ length: ROWS }, (_, i): FixtureEntity => ({
    id: `track-${i * 7919}`,
    class: CLASSES[i % CLASSES.length],
    score: ((i * 37) % 100) / 100,
    confidence: ((i * 53) % 100) / 100,
    motion: i % 3 === 0 ? "moving" : "stationary",
    velocity: [0.001 * (i % 5), 0],
    box: [0.1, 0.1, 0.2 + (i % 50) / 100, 0.3],
    attrs: {
      occluded: i % 4 === 0,
      carries_bag: i % 3 === 0,
      face_visible: (i % 10) / 10,
      vehicle_colors: [
        { rgba: 0xff0000ff, score: 0.4 },
        { rgba: 0x0000ffff, score: 0.9 },
      ],
      upper_clothing_colors: [{ rgba: 0x00ff00ff, score: 0.5 }],
      lower_clothing_colors: [],
    },
  })),
);

const lines: string[] = [];

/** Time `pass` (one sweep over all rows): median ns/row and bytes of heap growth per row across passes. */
function measure(label: string, pass: () => number): void {
  for (let i = 0; i < 30; i++) pass();
  const times: number[] = [];
  let grown = 0;
  let last = getHeapStatistics().used_heap_size;
  for (let i = 0; i < 60; i++) {
    const start = performance.now();
    pass();
    times.push(((performance.now() - start) * 1e6) / ROWS);
    const now = getHeapStatistics().used_heap_size;
    grown += Math.max(0, now - last);
    last = now;
  }
  times.sort((x, y) => x - y);
  lines.push(
    `${label.padEnd(66)} ${times[30].toFixed(1).padStart(8)} ns/row ${(grown / (60 * ROWS)).toFixed(1).padStart(6)} B/row`,
  );
}

describe.skipIf(!process.env.AX_BENCH)("benchmark", () => {
  it("times compiled expressions and catalogs", () => {
    for (const [source, type] of [
      ["score", "number"],
      ["score * 2 + 1", "number"],
      ["class == 'car'", "bool"],
      ["class in ['car', 'bus', 'truck']", "bool"],
      ["if(score > 0.5, 1, 2)", "number"],
      ["alpha(ramp(score, 0, #8c0f0f, 0.5, #ffa500, 1, #14ff78), 220)", "color"],
      ["palette(id, [#ffe100, #00dcff, #ff4d4d, #7cff4d])", "color"],
      ["top(attr.vehicle_colors)", "color"],
      ["trim(id, 10, 'middle')", "text"],
      ["fixed(score, 2)", "text"],
    ] as const) {
      const result = compileExpression(source, ENTITY, type);
      if (!result.ok) throw new Error(source);
      result.expression.ctx.bind(SAMPLE);
      const compiled = result.expression.compiled;
      const out = new CodePoints();
      measure(source, () => {
        let sink = 0;
        for (let row = 0; row < ROWS; row++) {
          if ("scalar" in compiled) sink += compiled.scalar.fn(row);
          else if (compiled.type === "text") {
            out.clear();
            sink += compiled.emit(row, out) ? out.length : 0;
          }
        }
        return sink;
      });
    }
    for (const name of ["standard", "classic", "glass"]) {
      const result = compileCatalog(
        readFileSync(
          new URL(`../../../../assets/catalogs/${name}.v4.json`, import.meta.url),
          "utf8",
        ),
      );
      if (!result.ok) throw new Error(name);
      result.catalog.ctx.bind(SAMPLE);
      const each = wholeRow(result.catalog, new CodePoints());
      measure(`${name}: variables and every mark field per entity`, () => {
        let sink = 0;
        for (let row = 0; row < ROWS; row++) sink += each(row);
        return sink;
      });
    }
    process.stdout.write(`${lines.join("\n")}\n`);
  });
});

/** Everything the evaluator reads per entity row of a catalog: the variables, then each mark's conditions and fields. */
function wholeRow(catalog: CompiledCatalog, out: CodePoints): (row: number) => number {
  // Constants are read once at layout, not called per row, so only the varying closures count.
  const numbers: ((row: number) => number)[] = [];
  const add = (scalar: Scalar | null | undefined): void => {
    if (scalar && scalar.constant === undefined) numbers.push(scalar.fn);
  };
  const texts: ((row: number, out: CodePoints) => boolean)[] = [];
  for (const layer of catalog.layers) {
    if (layer.relations) continue;
    add(layer.when);
    for (const mark of layer.marks) {
      for (const field of Object.values(mark.fields)) {
        if ("scalar" in field) add(field.scalar);
        else if (field.type === "length")
          for (const part of [field.length.px, field.length.k, field.length.lo, field.length.hi])
            add(part);
        else if (field.type === "text") texts.push(field.emit);
      }
      for (const run of mark.runs) if (run.text) texts.push(run.text);
      for (const place of mark.place) add(place.when);
    }
  }
  return (row) => {
    let sink = 0;
    computeVars(catalog, row, 0);
    for (const read of numbers) sink += read(row);
    for (const emit of texts) {
      out.clear();
      sink += emit(row, out) ? out.length : 0;
    }
    return sink;
  };
}
