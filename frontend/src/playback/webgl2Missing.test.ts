import { afterEach, expect, it, vi } from "vite-plus/test";
import { webgl2Missing } from "./webgl2Missing";

afterEach(() => vi.restoreAllMocks());

const agent = (userAgent: string) =>
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);

it("names the GPU and where to enable it in a browser, and the driver in any Electron window", () => {
  agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/156.0 Safari/537.36");
  const browser = webgl2Missing();
  expect(browser).toContain("GPU");
  expect(browser).toContain("Settings > System");
  expect(browser).toContain("Firefox");

  // The desktop window and the hidden export renderer (no preload bridge) both say Electron.
  agent(
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ax-devil/0.1.0 Chrome/156.0 Electron/45.2.1 Safari/537.36",
  );
  const desktop = webgl2Missing();
  expect(desktop).toContain("GPU");
  expect(desktop).toContain("driver");
  expect(desktop).not.toContain("Settings");
});
