/** Entry of `bench.html`: exposes the bench to the Electron script that drives it (`desktop/bench.cjs`). */

import { plan } from "./plan";
import { header, report } from "./report";
import { runScenario } from "./scenario";
import type { BenchApi, Environment } from "./types";

declare global {
  interface Window {
    benchApi: BenchApi & { header(environment: Environment): string };
  }
}

function environment(): Environment {
  const gl = document.createElement("canvas").getContext("webgl2");
  const info = gl?.getExtension("WEBGL_debug_renderer_info");
  const glRenderer = String(gl?.getParameter(info?.UNMASKED_RENDERER_WEBGL ?? 0) ?? "no WebGL2");
  const glVendor = String(gl?.getParameter(info?.UNMASKED_VENDOR_WEBGL ?? 0) ?? "");
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  return {
    glRenderer,
    glVendor,
    software: /swiftshader|llvmpipe|softpipe|software|basic render/i.test(glRenderer),
    userAgent: navigator.userAgent,
    cores: navigator.hardwareConcurrency,
    devicePixelRatio,
  };
}

window.benchApi = { environment, plan, run: runScenario, report, header };
