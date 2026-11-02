/** The GPU side of the render page: the viewer's renderer on an offscreen canvas, and the readback of its pictures. */

import type { OutputWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import type { VideoFrame } from "../playback/protocol";
import { VideoRenderer } from "../playback/renderer";
import type { Output } from "./output";
import { Readback } from "./readback";
import type { Stage } from "./session";

/** The renderer's name in the log: the GPU or the software renderer WebGL2 runs on. */
function rendererName(gl: WebGL2RenderingContext): string {
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  return String(gl.getParameter(info?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
}

/**
 * Draws at a device pixel ratio of 1, so a length in a catalog is a pixel of the output. Throws `RendererUnavailable`
 * when WebGL2 cannot be had.
 */
export class GlStage implements Stage {
  #drawing: { renderer: VideoRenderer; readback: Readback } | null = null;

  open(output: OutputWire, size: Output, names: readonly string[] | null): string {
    this.dispose();
    const canvas = document.createElement("canvas");
    const renderer = new VideoRenderer(canvas, 1);
    // The renderer made the context; asking the canvas again returns it.
    const gl = canvas.getContext("webgl2") as WebGL2RenderingContext;
    renderer.setCells(size.cells);
    renderer.setNames(names);
    renderer.resize(size.width, size.height);
    if (gl.drawingBufferWidth !== size.width || gl.drawingBufferHeight !== size.height) {
      renderer.dispose();
      throw new Error(`This renderer cannot draw ${size.width}x${size.height} pictures`);
    }
    try {
      this.#drawing = { renderer, readback: new Readback(gl, output, size.width, size.height) };
    } catch (error) {
      renderer.dispose();
      throw error;
    }
    return rendererName(gl);
  }

  present(frame: VideoFrame, lanes: readonly (LaneFrame | undefined)[]): void {
    this.#drawing?.renderer.present(frame, lanes);
  }

  read(seq: number): Uint8Array<ArrayBuffer> {
    if (!this.#drawing) throw new Error("The renderer is not set up");
    return this.#drawing.readback.read(seq);
  }

  dispose(): void {
    this.#drawing?.readback.dispose();
    this.#drawing?.renderer.dispose();
    this.#drawing = null;
  }
}
