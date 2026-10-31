import { defineConfig, lazyPlugins, loadEnv } from "vite-plus";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig(({ mode }) => ({
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
  fmt: { ignorePatterns: ["**/src/generated/**"] },
  lint: {
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    // App code reports through the UI or the backend logger, never the console.
    rules: { "vite-plus/prefer-vite-plus-imports": "error", "no-console": "error" },
    // Scripts and the desktop shell are command-line programs; their console is their interface.
    overrides: [{ files: ["desktop/**", "scripts/**"], rules: { "no-console": "off" } }],
    options: { typeAware: true, typeCheck: true },
  },
  plugins: lazyPlugins(() => [react(), tailwindcss()]),
  server: {
    port: 7331,
    strictPort: true,
    proxy: {
      "/api": { target: `http://127.0.0.1:${loadEnv(mode, ".", "").PORT || "7330"}`, ws: true },
    },
  },
}));
