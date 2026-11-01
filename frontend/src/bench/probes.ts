/**
 * Stage timers put around the real code without changing it: the lane presenter's evaluate and layout, and the GL
 * calls that move bytes or draw. The GL probes only run on frames that ask for them (`probe.gl`), so the frames that
 * measure the whole step pay for no timer calls inside GL.
 */

import { LanePresenter } from "../present";
import type { Stage } from "./types";

type Method = (this: unknown, ...args: unknown[]) => unknown;
type Timed = Exclude<Stage, "marks" | "present">;

export const probe = {
  /** Time the presenter's stages. */
  cpu: false,
  /** Time the GL calls. */
  gl: false,
  /** The video frame's size, to tell its plane uploads from the glyph atlas's. */
  video: { width: 0, height: 0 },
  ms: {
    filterEvaluate: 0,
    layoutQuads: 0,
    bufferUpload: 0,
    videoUpload: 0,
    atlasUpload: 0,
    draw: 0,
  } as Record<Timed, number>,
};

export function clearProbe(): void {
  for (const stage of Object.keys(probe.ms) as Timed[]) probe.ms[stage] = 0;
}

function wrap(
  owner: object,
  name: string,
  gate: "cpu" | "gl",
  stage: (args: unknown[]) => Timed,
): void {
  const methods = owner as Record<string, Method>;
  const original = methods[name];
  if (typeof original !== "function")
    throw new Error(`bench: ${name} no longer exists; update the probes`);
  methods[name] = function (this: unknown, ...args: unknown[]) {
    if (!probe[gate]) return Reflect.apply(original, this, args);
    const start = performance.now();
    try {
      return Reflect.apply(original, this, args);
    } finally {
      probe.ms[stage(args)] += performance.now() - start;
    }
  };
}

/** A texSubImage2D of a video plane has the frame's size or its chroma size. */
function uploadStage(args: unknown[]): Timed {
  const [width, height] = [args[4], args[5]];
  const { video } = probe;
  const luma = width === video.width && height === video.height;
  const chroma = width === Math.ceil(video.width / 2) && height === Math.ceil(video.height / 2);
  return luma || chroma ? "videoUpload" : "atlasUpload";
}

let installed = false;

/** Wraps the methods once; fails loudly when the code they time has been renamed, so the bench cannot go stale. */
export function installProbes(): void {
  if (installed) return;
  installed = true;
  wrap(LanePresenter.prototype, "evaluate", "cpu", () => "filterEvaluate");
  wrap(LanePresenter.prototype, "layout", "cpu", () => "layoutQuads");
  const gl = WebGL2RenderingContext.prototype;
  wrap(gl, "bufferData", "gl", () => "bufferUpload");
  wrap(gl, "texSubImage2D", "gl", uploadStage);
  wrap(gl, "drawArrays", "gl", () => "draw");
  wrap(gl, "drawArraysInstanced", "gl", () => "draw");
}
