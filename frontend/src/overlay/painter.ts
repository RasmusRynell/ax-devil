/**
 * Draws prepared drawings with WebGL2 on the video's own context, one lane cell at a time: polygon fills through the
 * stencil, then shape quads, then text and label quads sampling the atlas page. Each drawing is stretched from its
 * target size onto the picture it covers and clipped to its cell.
 */

import type { AtlasMirror } from "./mirror";
import type { PreparedDrawing } from "./prepare";
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
  readonly #quadBuffer: WebGLBuffer;
  readonly #fillBuffer: WebGLBuffer;
  readonly #quadArray: WebGLVertexArrayObject;
  readonly #fillArray: WebGLVertexArrayObject;
  #page: WebGLTexture | undefined;
  /** The mirror the page was filled from; another one (or none yet) means a full upload. */
  #mirror: AtlasMirror | undefined;

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
    this.#quadBuffer = gl.createBuffer();
    this.#quadArray = gl.createVertexArray();
    gl.bindVertexArray(this.#quadArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#quadBuffer);
    const stride = QUAD_FLOATS * 4;
    for (let location = 0; location < 5; location++) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 2, gl.FLOAT, false, stride, location * 8);
      gl.vertexAttribDivisor(location, 1);
    }
    for (const location of [5, 6]) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 4, gl.UNSIGNED_BYTE, true, stride, 40 + (location - 5) * 4);
      gl.vertexAttribDivisor(location, 1);
    }
    this.#fillBuffer = gl.createBuffer();
    this.#fillArray = gl.createVertexArray();
    gl.bindVertexArray(this.#fillArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#fillBuffer);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    gl.bindVertexArray(null);
  }

  /**
   * Draws `drawing` stretched onto `picture` and clipped to `cell`, both in canvas device pixels. Texts and labels
   * are drawn only when `atlas` holds the drawing's generation; a drawing made before an atlas reset draws its
   * shapes alone until its replacement arrives.
   */
  draw(
    drawing: PreparedDrawing,
    picture: CellRect,
    cell: CellRect,
    atlas: AtlasMirror | undefined,
  ): void {
    const gl = this.#gl;
    const { width, height } = drawing.target;
    if (!(width > 0 && height > 0)) return;
    const canvasWidth = gl.drawingBufferWidth;
    const canvasHeight = gl.drawingBufferHeight;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(cell.x, canvasHeight - cell.y - cell.height, cell.width, cell.height);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const scaleX = picture.width / width;
    const scaleY = picture.height / height;
    for (const target of drawing.polygons.length > 0 ? [this.#fills, this.#quads] : [this.#quads]) {
      gl.useProgram(target.program);
      gl.uniform2f(target.canvas, canvasWidth, canvasHeight);
      gl.uniform2f(target.offset, picture.x, picture.y);
      gl.uniform2f(target.scale, scaleX, scaleY);
      if (target === this.#fills) this.#drawFills(drawing);
    }
    gl.uniform1f(this.#uniforms.opacity, drawing.opacity);
    gl.bindVertexArray(this.#quadArray);
    this.#drawQuads(drawing.shapes);
    if (atlas && drawing.texts.length > 0 && drawing.generation === atlas.generation) {
      this.#syncPage(atlas);
      gl.uniform2f(this.#uniforms.atlas, 1 / atlas.size, drawing.dpr);
      this.#drawQuads(drawing.texts);
    }
    gl.bindVertexArray(null);
    gl.disable(gl.BLEND);
    gl.disable(gl.SCISSOR_TEST);
  }

  /** Frees the programs, buffers and the atlas page. */
  dispose(): void {
    const gl = this.#gl;
    gl.deleteProgram(this.#quads.program);
    gl.deleteProgram(this.#fills.program);
    gl.deleteBuffer(this.#quadBuffer);
    gl.deleteBuffer(this.#fillBuffer);
    gl.deleteVertexArray(this.#quadArray);
    gl.deleteVertexArray(this.#fillArray);
    if (this.#page) gl.deleteTexture(this.#page);
  }

  #drawQuads(quads: Float32Array): void {
    const count = quads.length / QUAD_FLOATS;
    if (count === 0) return;
    const gl = this.#gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#quadBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, quads, gl.STREAM_DRAW);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
  }

  /** Odd-even fills: each polygon's fan inverts the stencil, then the fan again paints where it is odd and clears it. */
  #drawFills(drawing: PreparedDrawing): void {
    const gl = this.#gl;
    const blocks = drawing.polygons;
    const words = new Uint32Array(blocks.buffer, blocks.byteOffset, blocks.length);
    gl.bindVertexArray(this.#fillArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#fillBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, blocks, gl.STREAM_DRAW);
    gl.enable(gl.STENCIL_TEST);
    for (let at = 0; at < blocks.length;) {
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
      const opacity = drawing.opacity / 255;
      gl.uniform4f(
        this.#uniforms.fill,
        (color & 0xff) * opacity,
        ((color >>> 8) & 0xff) * opacity,
        ((color >>> 16) & 0xff) * opacity,
        (color >>> 24) * opacity,
      );
      gl.drawArrays(gl.TRIANGLE_FAN, first, corners);
      at += 6 + corners * 2;
    }
    gl.disable(gl.STENCIL_TEST);
  }

  /** Copies what changed on the mirror onto the GPU page, or all of it the first time. */
  #syncPage(atlas: AtlasMirror): void {
    const gl = this.#gl;
    gl.activeTexture(gl.TEXTURE0 + ATLAS_UNIT);
    if (!this.#page) {
      this.#page = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.#page);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.R8, atlas.size, atlas.size);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    } else gl.bindTexture(gl.TEXTURE_2D, this.#page);
    const dirty = atlas.takeDirty();
    const [left, top, right, bottom] =
      this.#mirror === atlas ? (dirty ?? [0, 0, 0, 0]) : [0, 0, atlas.size, atlas.size];
    this.#mirror = atlas;
    if (right <= left || bottom <= top) return;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.pixelStorei(gl.UNPACK_ROW_LENGTH, atlas.size);
    gl.pixelStorei(gl.UNPACK_SKIP_PIXELS, left);
    gl.pixelStorei(gl.UNPACK_SKIP_ROWS, top);
    gl.texSubImage2D(
      gl.TEXTURE_2D,
      0,
      left,
      top,
      right - left,
      bottom - top,
      gl.RED,
      gl.UNSIGNED_BYTE,
      atlas.pixels,
    );
    gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
    gl.pixelStorei(gl.UNPACK_SKIP_PIXELS, 0);
    gl.pixelStorei(gl.UNPACK_SKIP_ROWS, 0);
  }
}
