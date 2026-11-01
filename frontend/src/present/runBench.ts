// Benchmark of the presentation path (filter, evaluate, layout, quads) in plain Node, no test runner:
//   make bench-present                                   (all built-in catalogs and sizes)
//   AX_BENCH_SIZES=1000 AX_BENCH_CATALOGS=standard AX_BENCH_MODES=warm AX_BENCH_REPEAT=3 make bench-present
// Modes: warm (each lane re-shows its sample), cold (new samples, empty atlas page each frame), hit (warm through the
// lane cache). The garbage column is exact only with big semi-spaces (see the Makefile target).
// Node cannot read the sources as they are (extensionless imports, parameter properties), so this registers a loader
// that finds `.ts` files and strips their types with the TypeScript compiler, the same output `tsc` would give.
import { createRequire, registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { BenchResult, Mode } from "./bench";

const ts = createRequire(import.meta.url)("typescript") as typeof import("typescript");
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      const missing = (error as { code?: string }).code === "ERR_MODULE_NOT_FOUND";
      if (!missing || specifier.startsWith("node:")) throw error;
      return next(`${specifier}.ts`, context);
    }
  },
  load(url, context, next) {
    if (!url.endsWith(".ts") || url.includes("/node_modules/")) return next(url, context);
    const { outputText } = ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    });
    return { format: "module", source: outputText, shortCircuit: true };
  },
});

const { scenario } = (await import("./bench")) as typeof import("./bench");

const sizes = (process.env.AX_BENCH_SIZES ?? "1,100,1000,5000").split(",").map(Number);
const modes = (process.env.AX_BENCH_MODES ?? "warm,cold,hit").split(",") as Mode[];
const repeats = Number(process.env.AX_BENCH_REPEAT ?? 1);
const catalogs = (process.env.AX_BENCH_CATALOGS ?? "standard,classic,glass").split(",");

const fixed = (value: number, digits = 2, width = 7): string =>
  value.toFixed(digits).padStart(width);
const header =
  "catalog   objects mode | 1 lane ms p50    p95 | 4 lanes ms p50    p95 | eval  layout | quad B/obj garbage KB/obj | minor GC n   ms";
const line = (r: BenchResult): string =>
  [
    `${r.catalog.padEnd(9)} ${String(r.count).padStart(7)} ${r.mode}`,
    `${fixed(r.lane.p50, 3)} ${fixed(r.lane.p95, 3)}`,
    `${fixed(r.lanes4.p50, 3)} ${fixed(r.lanes4.p95, 3)}`,
    `${fixed(r.evalP50, 3)} ${fixed(r.layoutP50, 3)}`,
    `${fixed(r.quadBytesPerObject, 0, 8)} ${fixed(r.garbageKbPerObject, 2, 11)}`,
    `${String(r.minorGcs).padStart(9)} ${fixed(r.minorGcMs, 1)}`,
  ].join(" | ");

/** The quietest of `repeats` runs: other work on the machine only ever makes a run slower. */
async function quietest(name: string, count: number, mode: Mode): Promise<BenchResult> {
  let best = await scenario(name, count, mode);
  for (let run = 1; run < repeats; run++) {
    const next = await scenario(name, count, mode);
    if (next.lanes4.p95 < best.lanes4.p95) best = next;
  }
  return best;
}

process.stdout.write(`${header}\n`);
for (const name of catalogs)
  for (const count of sizes)
    for (const mode of modes) process.stdout.write(`${line(await quietest(name, count, mode))}\n`);
