import { afterEach, expect, it, vi } from "vite-plus/test";
import type { WindowBridge } from "../client/windowBridge";
import { webgl2Missing } from "./webgl2Missing";

afterEach(() => vi.unstubAllGlobals());

it("points a browser at its acceleration setting and the desktop shell at the driver", () => {
  expect(webgl2Missing()).toContain("browser settings");
  vi.stubGlobal("axDevilWindow", {} as WindowBridge);
  expect(webgl2Missing()).toContain("driver");
});
