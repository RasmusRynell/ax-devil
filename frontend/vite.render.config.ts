import { defineConfig } from "vite-plus";

// The page the export loads in a hidden Electron window (desktop/render.cjs) from desktop/generated/render/render.html.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../desktop/generated/render",
    emptyOutDir: true,
    rollupOptions: { input: "render.html" },
  },
});
