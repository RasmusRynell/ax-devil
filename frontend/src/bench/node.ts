// Lets plain Node run the sources as they are, for the benchmarks that have no test runner. Load it before the program:
//   node --import ./frontend/src/bench/node.ts frontend/src/present/runBench.ts
// Node cannot read the sources as they are (extensionless imports, parameter properties), so this registers a loader
// that finds `.ts` files and folders' `index.ts` and strips their types with the TypeScript compiler, the same output `tsc` would give.
import { createRequire, registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const ts = createRequire(import.meta.url)("typescript") as typeof import("typescript");
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      const code = (error as { code?: string }).code;
      if (specifier.startsWith("node:")) throw error;
      if (code === "ERR_MODULE_NOT_FOUND") return next(`${specifier}.ts`, context);
      if (code === "ERR_UNSUPPORTED_DIR_IMPORT") return next(`${specifier}/index.ts`, context);
      throw error;
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
