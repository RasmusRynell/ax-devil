/**
 * Draws each lane's quads with WebGL2 on the video's own context, one lane cell at a time. A lane keeps its quads and
 * polygon fills in GPU buffers, uploaded again only when its layout changed. Layers draw in order, each layer's polygon
 * fills (through the stencil) and then its quads, which sample the atlas page. The quads are laid out in CSS pixels of
 * the displayed picture and are stretched onto the picture and clipped to the cell.
 */

import type { LaneQuads } from "../present";
import type { TextAtlas } from "./atlas";
import { QUAD_FLOATS } from "./quads";
import { FILL_FRAGMENT, FILL_VERTEX, QUAD_FRAGMENT, QUAD_VERTEX } from "./shaders";

/** A rectangle of the canvas in device pixels, from its top-left corner. */
export type CellRect = { x: number; y: number; width: number; height: number };

/** The texture unit of the atlas page; the video planes use 0 to 2. */
const ATLAS_UNIT = 3;

function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const created = gl.createProgram();
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("a shader cannot be created");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(shader) ?? "a shader does not compile");
    gl.attachShader(created, shader);
    gl.deleteShader(shader);
  }
  gl.linkProgram(created);
  if (!gl.getProgramParameter(created, gl.LINK_STATUS))
    throw new Error(gl.getProgramInfoLog(created) ?? "a program does not link");
  return created;
}

type Placed = {
  program: WebGLProgram;
  canvas: WebGLUniformLocation | null;
  offset: WebGLUniformLocation | null;
  scale: WebGLUniformLocation | null;
};

function placed(gl: WebGL2RenderingContext, created: WebGLProgram): Placed {
  const at = (name: string) => gl.getUniformLocation(created, name);
  return { program: created, canvas: at("canvas"), offset: at("offset"), scale: at("scale") };
}

/** A lane's layout as the painter takes it. */
export type LaneDrawing = {
  /** Names the lane's GPU buffers. */
  lane: number;
  quads: LaneQuads;
  /** Changes whenever `quads` changed, so the buffers are uploaded again. */
  version: number;
  /** The displayed picture the quads were laid out for, in CSS pixels, and the device pixel ratio of its glyphs. */
  view: { width: number; height: number; dpr: number };
  /** 0 to 1, applied to everything the lane draws. */
  opacity: number;
};

type LaneBuffers = { quads: WebGLBuffer; fills: WebGLBuffer; source: LaneQuads; version: number };

const QUAD_STRIDE = QUAD_FLOATS * 4;

/** The overlay painter of one WebGL2 context. */
export class OverlayPainter {
  readonly #gl: WebGL2RenderingContext;
  readonly #quads: Placed;
  readonly #fills: Placed;
  readonly #uniforms: {
    atlas: WebGLUniformLocation | null;
    opacity: WebGLUniformLocation | null;
    fill: WebGLUniformLocation | null;
  };
  readonly #quadArray: WebGLVertexArrayObject;
  readonly #fillArray: WebGLVertexArrayObject;
  readonly #lanes = new Map<number, LaneBuffers>();
  #page: WebGLTexture | undefined;
  #pageSize = 0;

  /** Compiles the overlay programs; throws when the context cannot. */
  constructor(gl: WebGL2RenderingContext) {
    this.#gl = gl;
    this.#quads = placed(gl, program(gl, QUAD_VERTEX, QUAD_FRAGMENT));
    this.#fills = placed(gl, program(gl, FILL_VERTEX, FILL_FRAGMENT));
    this.#uniforms = {
      atlas: gl.getUniformLocation(this.#quads.program, "atlas"),
      opacity: gl.getUniformLocation(this.#quads.program, "opacity"),
      fill: gl.getUniformLocation(this.#fills.program, "fill"),
    };
    gl.useProgram(this.#quads.program);
    gl.uniform1i(gl.getUniformLocation(this.#quads.program, "page"), ATLAS_UNIT);
    // The attribute pointers are set per draw, because they name the lane's buffer and the first quad of a layer.
    this.#quadArray = gl.createVertexArray();
    gl.bindVertexArray(this.#quadArray);
    for (let location = 0; location < 7; location++) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribDivisor(location, 1);
    }
    this.#fillArray = gl.createVertexArray();
    gl.bindVertexArray(this.#fillArray);
    gl.enableVertexAttribArray(0);
    gl.bindVertexArray(null);
  }

  /** Copies what the glyph atlas rasterized since the last call onto the GPU page. Call it before drawing. */
  sync(atlas: TextAtlas): void {
    const gl = this.#gl;
    gl.activeTexture(gl.TEXTURE0 + ATLAS_UNIT);
    if (!this.#page) {
      this.#page = gl.createTexture();
      this.#pageSize = atlas.size;
      gl.bindTexture(gl.TEXTURE_2D, this.#page);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.R8, atlas.size, atlas.size);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    } else gl.bindTexture(gl.TEXTURE_2D, this.#page);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    // A reset needs no work here: every lane is laid out again on the new page before it is drawn.
    for (const upload of atlas.takeUploads())
      if (upload.type === "region")
        gl.texSubImage2D(
          gl.TEXTURE_2D,
          0,
          upload.x,
          upload.y,
          upload.width,
          upload.height,
          gl.RED,
          gl.UNSIGNED_BYTE,
          upload.alpha,
        );
  }

  /**
   * Draws the lane stretched onto `picture` and clipped to `cell`, both in canvas device pixels, layer after layer.
   */
  draw(drawing: LaneDrawing, picture: CellRect, cell: CellRect): void {
    const { quads, view } = drawing;
    const count = quads.quads.count;
    if (!(view.width > 0 && view.height > 0) || (count === 0 && quads.fills.length === 0)) return;
    const gl = this.#gl;
    const buffers = this.#upload(drawing);
    const canvasWidth = gl.drawingBufferWidth;
    const canvasHeight = gl.drawingBufferHeight;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(cell.x, canvasHeight - cell.y - cell.height, cell.width, cell.height);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    for (const target of [this.#fills, this.#quads]) {
      gl.useProgram(target.program);
      gl.uniform2f(target.canvas, canvasWidth, canvasHeight);
      gl.uniform2f(target.offset, picture.x, picture.y);
      gl.uniform2f(target.scale, picture.width / view.width, picture.height / view.height);
    }
    gl.uniform1f(this.#uniforms.opacity, drawing.opacity);
    gl.uniform2f(this.#uniforms.atlas, 1 / this.#pageSize, view.dpr);
    // Whatever follows the last closed layer draws with it.
    let quadFrom = 0;
    let fillFrom = 0;
    for (let layer = 0; layer <= quads.layerCount; layer++) {
      const last = layer === quads.layerCount;
      const quadTo = last ? count : quads.layerQuads[layer];
      const fillTo = last ? quads.fills.length : quads.layerFills[layer];
      this.#drawFills(buffers, quads, fillFrom, fillTo, drawing.opacity);
      this.#drawQuads(buffers, quadFrom, quadTo);
      quadFrom = quadTo;
      fillFrom = fillTo;
    }
    gl.bindVertexArray(null);
    gl.disable(gl.BLEND);
    gl.disable(gl.SCISSOR_TEST);
  }

  /** Frees the buffers of every lane from `count` on. */
  keepLanes(count: number): void {
    for (const lane of this.#lanes.keys()) if (lane >= count) this.#free(lane);
  }

  /** Frees the programs, buffers and the atlas page. */
  dispose(): void {
    const gl = this.#gl;
    for (const lane of this.#lanes.keys()) this.#free(lane);
    gl.deleteProgram(this.#quads.program);
    gl.deleteProgram(this.#fills.program);
    gl.deleteVertexArray(this.#quadArray);
    gl.deleteVertexArray(this.#fillArray);
    if (this.#page) gl.deleteTexture(this.#page);
  }

  #free(lane: number): void {
    const held = this.#lanes.get(lane);
    if (!held) return;
    this.#gl.deleteBuffer(held.quads);
    this.#gl.deleteBuffer(held.fills);
    this.#lanes.delete(lane);
  }

  /** The lane's buffers, filled again when its quads are not the ones they were filled from. */
  #upload({ lane, quads, version }: LaneDrawing): LaneBuffers {
    const gl = this.#gl;
    let held = this.#lanes.get(lane);
    if (!held) {
      held = { quads: gl.createBuffer(), fills: gl.createBuffer(), source: quads, version: -1 };
      this.#lanes.set(lane, held);
    }
    if (held.source !== quads || held.version !== version) {
      gl.bindBuffer(gl.ARRAY_BUFFER, held.quads);
      gl.bufferData(gl.ARRAY_BUFFER, quads.quads.subarray(), gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, held.fills);
      gl.bufferData(gl.ARRAY_BUFFER, quads.fills.view(), gl.DYNAMIC_DRAW);
      held.source = quads;
      held.version = version;
    }
    return held;
  }

  /** Quads `from` up to `to` of the lane's buffer. */
  #drawQuads(buffers: LaneBuffers, from: number, to: number): void {
    if (to <= from) return;
    const gl = this.#gl;
    gl.useProgram(this.#quads.program);
    gl.bindVertexArray(this.#quadArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.quads);
    // Instanced attributes ignore the draw call's first vertex, so the layer's start is the pointers' offset.
    const base = from * QUAD_STRIDE;
    for (let location = 0; location < 5; location++)
      gl.vertexAttribPointer(location, 2, gl.FLOAT, false, QUAD_STRIDE, base + location * 8);
    for (const location of [5, 6])
      gl.vertexAttribPointer(
        location,
        4,
        gl.UNSIGNED_BYTE,
        true,
        QUAD_STRIDE,
        base + 40 + (location - 5) * 4,
      );
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, to - from);
  }

  /** Odd-even fills: each polygon's fan inverts the stencil, then the fan again paints where it is odd and clears it. */
  #drawFills(
    buffers: LaneBuffers,
    { fills }: LaneQuads,
    from: number,
    to: number,
    opacity: number,
  ): void {
    if (to <= from) return;
    const gl = this.#gl;
    const blocks = fills.view();
    const words = new Uint32Array(blocks.buffer, blocks.byteOffset, blocks.length);
    gl.useProgram(this.#fills.program);
    gl.bindVertexArray(this.#fillArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.fills);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    gl.enable(gl.STENCIL_TEST);
    for (let at = from; at < to;) {
      const corners = blocks[at];
      const color = words[at + 1];
      const first = (at + 6) / 2;
      gl.colorMask(false, false, false, false);
      gl.stencilFunc(gl.ALWAYS, 0, 0xff);
      gl.stencilOp(gl.KEEP, gl.KEEP, gl.INVERT);
      gl.drawArrays(gl.TRIANGLE_FAN, first, corners);
      gl.colorMask(true, true, true, true);
      gl.stencilFunc(gl.NOTEQUAL, 0, 0xff);
      gl.stencilOp(gl.ZERO, gl.ZERO, gl.ZERO);
      const scale = opacity / 255;
      gl.uniform4f(
        this.#uniforms.fill,
        (color & 0xff) * scale,
        ((color >>> 8) & 0xff) * scale,
        ((color >>> 16) & 0xff) * scale,
        (color >>> 24) * scale,
      );
      gl.drawArrays(gl.TRIANGLE_FAN, first, corners);
      at += 6 + corners * 2;
    }
    gl.disable(gl.STENCIL_TEST);
  }
}
