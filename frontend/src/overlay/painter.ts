/**
 * Draws each lane's quads with WebGL2 on the video's own context, one lane cell at a time. A lane keeps its quads and
 * polygon fills in GPU buffers, uploaded again only when its layout changed. Layers draw in order, each layer's polygon
 * fills (through the stencil) and then its quads, which sample the atlas page. The quads are laid out in CSS pixels of
 * the displayed picture and are stretched onto the picture and clipped to the cell.
 */

import type { LaneQuads } from "../present";
import type { TextAtlas } from "./atlas";
import { FILL_HEADER_FLOATS, FILL_VERTEX_FLOATS, QUAD_FLOATS } from "./quads";
import { FadeLayer } from "./layer";
import { FILL_FRAGMENT, FILL_VERTEX, QUAD_FRAGMENT, QUAD_VERTEX, compileProgram } from "./shaders";

/** A rectangle of the canvas in device pixels, from its top-left corner. */
export type CellRect = { x: number; y: number; width: number; height: number };

/** The texture unit of the atlas page; the video planes use 0 to 2. */
const ATLAS_UNIT = 3;

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
  /** 0 to 1, applied once to everything the lane draws, so overlapping marks do not darken each other. */
  opacity: number;
};

type LaneBuffers = { quads: WebGLBuffer; fills: WebGLBuffer; source: LaneQuads; version: number };

const QUAD_STRIDE = QUAD_FLOATS * 4;
const FILL_STRIDE = FILL_VERTEX_FLOATS * 4;

/** The overlay painter of one WebGL2 context. */
export class OverlayPainter {
  readonly #gl: WebGL2RenderingContext;
  readonly #quads: Placed;
  readonly #fills: Placed;
  readonly #uniforms: {
    atlas: WebGLUniformLocation | null;
    fill: WebGLUniformLocation | null;
    edge: WebGLUniformLocation | null;
  };
  readonly #quadArray: WebGLVertexArrayObject;
  readonly #fillArray: WebGLVertexArrayObject;
  readonly #lanes = new Map<number, LaneBuffers>();
  readonly #fade = new FadeLayer();
  #page: WebGLTexture | undefined;
  #pageSize = 0;

  /** Compiles the overlay programs; throws when the context cannot. */
  constructor(gl: WebGL2RenderingContext) {
    this.#gl = gl;
    this.#quads = placed(gl, compileProgram(gl, QUAD_VERTEX, QUAD_FRAGMENT));
    this.#fills = placed(gl, compileProgram(gl, FILL_VERTEX, FILL_FRAGMENT));
    this.#uniforms = {
      atlas: gl.getUniformLocation(this.#quads.program, "atlas"),
      fill: gl.getUniformLocation(this.#fills.program, "fill"),
      edge: gl.getUniformLocation(this.#fills.program, "edge"),
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
    gl.enableVertexAttribArray(1);
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
    const { quads, view, opacity } = drawing;
    const count = quads.quads.count;
    if (
      !(view.width > 0 && view.height > 0) ||
      (count === 0 && quads.fills.length === 0) ||
      !(opacity > 0)
    )
      return;
    const gl = this.#gl;
    // A faded lane is drawn at full strength into a layer of its own and then blended once at its opacity. The layer
    // is made ready first, so a context that cannot make it fails before the draw changes any state.
    const faded = opacity < 1;
    if (faded) this.#fade.prepare(gl);
    const buffers = this.#upload(drawing);
    const canvasWidth = gl.drawingBufferWidth;
    const canvasHeight = gl.drawingBufferHeight;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(cell.x, canvasHeight - cell.y - cell.height, cell.width, cell.height);
    gl.enable(gl.BLEND);
    try {
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      if (faded) this.#fade.begin(gl);
      for (const target of [this.#fills, this.#quads]) {
        gl.useProgram(target.program);
        gl.uniform2f(target.canvas, canvasWidth, canvasHeight);
        gl.uniform2f(target.offset, picture.x, picture.y);
        gl.uniform2f(target.scale, picture.width / view.width, picture.height / view.height);
      }
      gl.uniform2f(this.#uniforms.atlas, 1 / this.#pageSize, view.dpr);
      // Whatever follows the last closed layer draws with it.
      let quadFrom = 0;
      let fillFrom = 0;
      for (let layer = 0; layer <= quads.layerCount; layer++) {
        const last = layer === quads.layerCount;
        const quadTo = last ? count : quads.layerQuads[layer];
        const fillTo = last ? quads.fills.length : quads.layerFills[layer];
        this.#drawFills(buffers, quads, fillFrom, fillTo, view.width / picture.width);
        this.#drawQuads(buffers, quadFrom, quadTo);
        quadFrom = quadTo;
        fillFrom = fillTo;
      }
      gl.bindVertexArray(null);
      if (faded) this.#fade.composite(gl, opacity);
    } finally {
      // Whatever went wrong, the canvas is what the next draw finds bound, with the state it expects.
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.bindVertexArray(null);
      gl.colorMask(true, true, true, true);
      gl.disable(gl.STENCIL_TEST);
      gl.disable(gl.BLEND);
      gl.disable(gl.SCISSOR_TEST);
    }
  }

  /** Frees the buffers of every lane from `count` on. */
  keepLanes(count: number): void {
    for (const lane of this.#lanes.keys()) if (lane >= count) this.#free(lane);
  }

  /** Frees the programs, buffers, the fade layer and the atlas page. */
  dispose(): void {
    const gl = this.#gl;
    for (const lane of this.#lanes.keys()) this.#free(lane);
    this.#fade.dispose(gl);
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

  /**
   * Odd-even fills: each polygon's fan inverts the stencil, then the fan again paints where it is odd and clears it.
   * The fan is the polygon pulled in by half a device pixel and the ring around it fades out half a device pixel
   * beyond the true edge, so the edge is antialiased; a polygon thinner than that band keeps its own edge as the fan.
   */
  #drawFills(
    buffers: LaneBuffers,
    { fills }: LaneQuads,
    from: number,
    to: number,
    /** The size of one device pixel in the lane's CSS pixels. */
    pixel: number,
  ): void {
    if (to <= from) return;
    const gl = this.#gl;
    const blocks = fills.view();
    const words = new Uint32Array(blocks.buffer, blocks.byteOffset, blocks.length);
    gl.useProgram(this.#fills.program);
    gl.bindVertexArray(this.#fillArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.fills);
    for (let at = from; at < to;) {
      const corners = blocks[at];
      const color = words[at + 1];
      const fringe = pixel / 2;
      gl.uniform2f(this.#uniforms.edge, fringe, blocks[at + 2] < fringe ? 0 : fringe);
      const base = (at + FILL_HEADER_FLOATS) * 4;
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, FILL_STRIDE, base);
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, FILL_STRIDE, base + 8);
      gl.enable(gl.STENCIL_TEST);
      gl.colorMask(false, false, false, false);
      gl.stencilFunc(gl.ALWAYS, 0, 0xff);
      gl.stencilOp(gl.KEEP, gl.KEEP, gl.INVERT);
      gl.drawArrays(gl.TRIANGLE_FAN, 0, corners);
      gl.colorMask(true, true, true, true);
      gl.stencilFunc(gl.NOTEQUAL, 0, 0xff);
      gl.stencilOp(gl.ZERO, gl.ZERO, gl.ZERO);
      const scale = 1 / 255;
      gl.uniform4f(
        this.#uniforms.fill,
        (color & 0xff) * scale,
        ((color >>> 8) & 0xff) * scale,
        ((color >>> 16) & 0xff) * scale,
        (color >>> 24) * scale,
      );
      gl.drawArrays(gl.TRIANGLE_FAN, 0, corners);
      gl.disable(gl.STENCIL_TEST);
      gl.drawArrays(gl.TRIANGLE_STRIP, corners, 2 * corners + 2);
      at += FILL_HEADER_FLOATS + (3 * corners + 2) * FILL_VERTEX_FLOATS;
    }
  }
}
