/**
 * Draws decoded frames on a canvas with WebGL2. Planar and semi-planar YUV frames are converted to RGB in a shader
 * from the matrix and range in the frame header; RGBA frames are drawn as they are. The canvas holds one cell per
 * lane: the frame is uploaded once and drawn into every cell, fitted keeping its aspect on a near-black field, and
 * each lane's overlay is painted over its picture on the same context.
 */

import type { AtlasMirror } from "../overlay/mirror";
import { OverlayPainter, type CellRect } from "../overlay/painter";
import type { PreparedDrawing } from "../overlay/prepare";
import type { Matrix, VideoFrame } from "./protocol";

export type { CellRect } from "../overlay/painter";

/** Where a lane is drawn on the canvas, in device pixels. */
export type LaneCell = { lane: number; rect: CellRect };

/** The picture of `size` fitted into `cell` keeping its aspect, centred, on whole pixels. */
export function fitPicture(cell: CellRect, size: { width: number; height: number }): CellRect {
  const scale = Math.min(cell.width / size.width, cell.height / size.height);
  const width = Math.max(1, Math.floor(size.width * scale));
  const height = Math.max(1, Math.floor(size.height * scale));
  return {
    x: cell.x + Math.floor((cell.width - width) / 2),
    y: cell.y + Math.floor((cell.height - height) / 2),
    width,
    height,
  };
}

/** WebGL2 cannot be had: the browser has no support, or has disabled it. */
export class RendererUnavailable extends Error {
  constructor(reason: string) {
    super(reason);
    this.name = "RendererUnavailable";
  }
}

/** Luma weights of red and blue by matrix; green is the rest. */
const LUMA_WEIGHTS: Record<Matrix, { kr: number; kb: number }> = {
  bt601: { kr: 0.299, kb: 0.114 },
  bt709: { kr: 0.2126, kb: 0.0722 },
};

/** The canvas token's near-black, `--color-canvas`, as GL color components. */
const FIELD = 0x0a / 255;

const VERTEX = `#version 300 es
in vec2 corner;
out vec2 uv;
void main() {
  uv = vec2(corner.x * .5 + .5, .5 - corner.y * .5);
  gl_Position = vec4(corner, 0., 1.);
}`;

// mode 0: planar Y, U, V. mode 1: Y, then interleaved UV in the second texture. mode 2: RGBA in the first.
const FRAGMENT = `#version 300 es
precision highp float;
in vec2 uv;
out vec4 color;
uniform sampler2D plane0, plane1, plane2;
uniform int mode;
uniform float kr, kb, limited;
void main() {
  if (mode == 2) { color = vec4(texture(plane0, uv).rgb, 1.); return; }
  float y = texture(plane0, uv).r;
  vec2 chroma = mode == 1 ? texture(plane1, uv).rg : vec2(texture(plane1, uv).r, texture(plane2, uv).r);
  float u = chroma.x - .5, v = chroma.y - .5;
  if (limited > .5) { y = (y - 16. / 255.) * 255. / 219.; u *= 255. / 224.; v *= 255. / 224.; }
  float r = y + 2. * (1. - kr) * v;
  float b = y + 2. * (1. - kb) * u;
  float g = (y - kr * r - kb * b) / (1. - kr - kb);
  color = vec4(r, g, b, 1.);
}`;

type Planes = { key: string; textures: WebGLTexture[] };

/** One viewer's canvas: the video in every lane cell and each lane's overlay over it. */
export class VideoRenderer {
  readonly #canvas: HTMLCanvasElement;
  readonly #gl: WebGL2RenderingContext;
  readonly #program: WebGLProgram;
  readonly #uniforms: Record<string, WebGLUniformLocation | null> = {};
  readonly #quad: WebGLVertexArrayObject;
  readonly #painter: OverlayPainter;
  #planes: Planes | undefined;
  #picture: { width: number; height: number } | undefined;
  #frame: VideoFrame | undefined;
  /** `null` is one cell for lane 0 covering the canvas. */
  #cells: readonly LaneCell[] | null = null;
  #overlays: readonly (PreparedDrawing | undefined)[] = [];
  #atlas: AtlasMirror | undefined;

  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      stencil: true,
      powerPreference: "high-performance",
    });
    if (!gl) throw new RendererUnavailable("WebGL2 is not available");
    this.#canvas = canvas;
    this.#gl = gl;
    const program = gl.createProgram();
    for (const [type, source] of [
      [gl.VERTEX_SHADER, VERTEX],
      [gl.FRAGMENT_SHADER, FRAGMENT],
    ] as const) {
      const shader = gl.createShader(type) as WebGLShader;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
        throw new RendererUnavailable(gl.getShaderInfoLog(shader) ?? "a shader does not compile");
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    gl.useProgram(program);
    this.#program = program;
    this.#quad = gl.createVertexArray();
    gl.bindVertexArray(this.#quad);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, "corner");
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    for (const name of ["plane0", "plane1", "plane2", "mode", "kr", "kb", "limited"])
      this.#uniforms[name] = gl.getUniformLocation(program, name);
    gl.uniform1i(this.#uniforms.plane0, 0);
    gl.uniform1i(this.#uniforms.plane1, 1);
    gl.uniform1i(this.#uniforms.plane2, 2);
    gl.bindVertexArray(null);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    try {
      this.#painter = new OverlayPainter(gl);
    } catch (error) {
      throw new RendererUnavailable(
        error instanceof Error ? error.message : "overlays cannot be drawn",
      );
    }
  }

  /** Sets the lane cells, `null` for one cell covering the canvas, and draws again. */
  setCells(cells: readonly LaneCell[] | null): void {
    this.#cells = cells;
    this.redraw();
  }

  /** Sets the canvas to this many device pixels and draws the picture again. */
  resize(width: number, height: number): void {
    if (this.#canvas.width !== width) this.#canvas.width = width;
    if (this.#canvas.height !== height) this.#canvas.height = height;
    this.redraw();
  }

  /**
   * Draws the frame in every cell with each cell's lane overlay from `overlays` (by lane) over it, their texts from
   * `atlas`. The frame is uploaded only when it is not the one drawn last.
   */
  present(
    frame: VideoFrame,
    overlays: readonly (PreparedDrawing | undefined)[] = [],
    atlas?: AtlasMirror,
  ): void {
    this.#overlays = overlays;
    this.#atlas = atlas;
    if (frame !== this.#frame) this.#upload(frame);
    this.redraw();
  }

  #upload(frame: VideoFrame): void {
    const gl = this.#gl;
    gl.useProgram(this.#program);
    const { format, width, height } = frame;
    const chromaWidth = Math.ceil(width / 2);
    const chromaHeight = Math.ceil(height / 2);
    const key = `${format}:${width}x${height}`;
    if (this.#planes?.key !== key) {
      this.#dropPlanes();
      const texture = (internal: number, w: number, h: number) => {
        const created = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, created);
        gl.texStorage2D(gl.TEXTURE_2D, 1, internal, w, h);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return created;
      };
      const textures =
        format === "rgba"
          ? [texture(gl.RGBA8, width, height)]
          : format === "nv12"
            ? [texture(gl.R8, width, height), texture(gl.RG8, chromaWidth, chromaHeight)]
            : [
                texture(gl.R8, width, height),
                texture(gl.R8, chromaWidth, chromaHeight),
                texture(gl.R8, chromaWidth, chromaHeight),
              ];
      this.#planes = { key, textures };
    }
    const upload = (
      unit: number,
      w: number,
      h: number,
      bytesPerPixel: number,
      pixelFormat: number,
      plane: number,
    ) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, (this.#planes as Planes).textures[unit]);
      gl.pixelStorei(gl.UNPACK_ROW_LENGTH, frame.strides[plane] / bytesPerPixel);
      gl.texSubImage2D(
        gl.TEXTURE_2D,
        0,
        0,
        0,
        w,
        h,
        pixelFormat,
        gl.UNSIGNED_BYTE,
        frame.planes[plane],
      );
    };
    if (format === "rgba") upload(0, width, height, 4, gl.RGBA, 0);
    else {
      upload(0, width, height, 1, gl.RED, 0);
      if (format === "nv12") upload(1, chromaWidth, chromaHeight, 2, gl.RG, 1);
      else {
        upload(1, chromaWidth, chromaHeight, 1, gl.RED, 1);
        upload(2, chromaWidth, chromaHeight, 1, gl.RED, 2);
      }
    }
    gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
    const { kr, kb } = LUMA_WEIGHTS[frame.matrix];
    gl.uniform1i(this.#uniforms.mode, format === "rgba" ? 2 : format === "nv12" ? 1 : 0);
    gl.uniform1f(this.#uniforms.kr, kr);
    gl.uniform1f(this.#uniforms.kb, kb);
    gl.uniform1f(this.#uniforms.limited, frame.fullRange ? 0 : 1);
    this.#picture = { width, height };
    this.#frame = frame;
  }

  /** Draws the last frame and overlays again, fitted to the canvas and cells as they are now. */
  redraw(): void {
    const gl = this.#gl;
    const { width: canvasWidth, height: canvasHeight } = this.#canvas;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.clearColor(FIELD, FIELD, FIELD, 1);
    gl.clearStencil(0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT);
    const picture = this.#picture;
    if (!picture || !this.#planes) return;
    const cells = this.#cells ?? [
      { lane: 0, rect: { x: 0, y: 0, width: canvasWidth, height: canvasHeight } },
    ];
    for (const { lane, rect } of cells) {
      if (rect.width <= 0 || rect.height <= 0) continue;
      const fitted = fitPicture(rect, picture);
      gl.useProgram(this.#program);
      gl.bindVertexArray(this.#quad);
      gl.viewport(fitted.x, canvasHeight - fitted.y - fitted.height, fitted.width, fitted.height);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.bindVertexArray(null);
      const overlay = this.#overlays[lane];
      if (overlay) this.#painter.draw(overlay, fitted, rect, this.#atlas);
    }
  }

  /** Frees the GPU's copy of the picture and the overlay painter. */
  dispose(): void {
    this.#dropPlanes();
    this.#painter.dispose();
    this.#gl.deleteVertexArray(this.#quad);
    this.#gl.deleteProgram(this.#program);
  }

  #dropPlanes(): void {
    this.#planes?.textures.forEach((texture) => this.#gl.deleteTexture(texture));
    this.#planes = undefined;
    this.#frame = undefined;
  }
}
