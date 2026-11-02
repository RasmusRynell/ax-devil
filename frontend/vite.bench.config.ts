import { defineConfig } from "vite-plus";

// The page `make bench-browser` loads in Electron (dev only; never part of the release bundle).
export default defineConfig({
  base: "./",
  build: {
    outDir: "../artifacts/bench-browser/site",
    emptyOutDir: true,
    rollupOptions: { input: "bench.html" },
  },
});
