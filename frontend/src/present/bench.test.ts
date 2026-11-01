// @vitest-environment node
// Benchmark of the whole presentation path (filter, evaluate, layout, quads) per lane frame, skipped in the normal suite:
//   AX_BENCH=1 NODE_OPTIONS=--expose-gc pnpm --dir frontend exec vp test run src/present/bench.test.ts
//   AX_BENCH_SIZES=1000 AX_BENCH_CATALOGS=standard to narrow it
// Four lanes are four sequential runs per frame. "Warm" shows the same sample every frame with its glyphs known to the
// atlas; "cold" shows four samples in turn (other ids) and starts every frame on an empty atlas page. Prints
// milliseconds per lane frame (p50, p95) and per four-lane frame, the garbage made (exact only with
// NODE_OPTIONS="--expose-gc --min-semi-space-size=128 --max-semi-space-size=128": no scavenge inside the measured
// frame) and the minor-GC pause time. This runner is about twice as slow as the same code compiled with tsc and run by
// plain node; judge the budgets by the plain run.
import { describe, it } from "vite-plus/test";
import { scenario } from "./bench";

const sizes = (process.env.AX_BENCH_SIZES ?? "1,100,1000,5000").split(",").map(Number);
const catalogs = (process.env.AX_BENCH_CATALOGS ?? "standard,classic,glass").split(",");

describe.skipIf(!process.env.AX_BENCH)("present benchmark", () => {
  it("times a four-lane frame per catalog and size", { timeout: 1_800_000 }, async () => {
    const lines = ["ms per lane frame: p50 p95 (4 lanes: per frame of four sequential lanes)"];
    for (const name of catalogs)
      for (const count of sizes)
        for (const cold of [false, true]) lines.push(await scenario(name, count, cold));
    process.stdout.write(`${lines.join("\n")}\n`);
  });
});
