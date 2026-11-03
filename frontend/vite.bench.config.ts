import { defineConfig, lazyPlugins } from "vite-plus";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The page `make bench-browser` loads in Electron (dev only; never part of the release bundle).
export default defineConfig({
  base: "./",
  // The tables suite mounts the real objects panel, which needs React and the app's styles.
  plugins: lazyPlugins(() => [react(), tailwindcss()]),
  build: {
    outDir: "../artifacts/bench-browser/site",
    emptyOutDir: true,
    rollupOptions: { input: "bench.html" },
  },
});
