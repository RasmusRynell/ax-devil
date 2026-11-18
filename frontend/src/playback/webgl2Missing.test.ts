import { afterEach, expect, it, vi } from "vite-plus/test";
import type { WindowBridge } from "../client/windowBridge";
import { webgl2Missing } from "./webgl2Missing";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const agent = (userAgent: string) =>
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);

it("points a browser at its acceleration setting and any Electron window at the driver", () => {
  agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/156.0 Safari/537.36");
  expect(webgl2Missing()).toContain("browser settings");

  agent(
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ax-devil/0.1.0 Chrome/156.0 Electron/45.2.1 Safari/537.36",
  );
  vi.stubGlobal("axDevilWindow", {} as WindowBridge);
  expect(webgl2Missing()).toContain("driver");
  // The hidden export renderer has no preload bridge.
  vi.unstubAllGlobals();
  expect(webgl2Missing()).toContain("driver");
});
