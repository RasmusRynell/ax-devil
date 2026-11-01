import { defineConfig } from "vite-plus";

// The bundle `ax-devil catalog check` runs in Electron-as-Node: one CommonJS file with the compiler inside.
export default defineConfig({
  build: {
    ssr: "src/cli/catalogCheck.ts",
    outDir: "../desktop/generated",
    emptyOutDir: false,
    minify: false,
    rollupOptions: { output: { format: "cjs", entryFileNames: "catalog-check.cjs" } },
  },
  ssr: { noExternal: true },
});
