/**
 * Reads the picture the renderer drew back as a Pixels message. A still reads the canvas as RGBA. Video is converted
 * on the GPU to I420 (BT.709, limited range, each chroma sample the average of its 2x2 block) so 1.5 bytes a pixel
 * cross the GPU boundary instead of 4: a pass renders the picture's bytes into an RGBA texture, four bytes a texel,
 * and one `readPixels` takes the whole texture.
 */

import type { OutputWire } from "../generated/api";
import { PixelsMessage, planesOf } from "./pixels";

/** The texture unit of the picture; the renderer's video planes and glyph atlas use 0 to 3. */
const PICTURE_UNIT = 4;

const VERTEX = `#version 300 es
void main() {
  vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(corner * 2. - 1., 0., 1.);
}`;

// geometry: width, height, luma stride, chroma stride. The output texel at (x, y) holds bytes 4n to 4n+3 of the planes,
// n = y * (luma stride / 4) + x: the luma rows, then the U rows, then the V rows.
const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
uniform sampler2D picture;
uniform ivec4 geometry;
out vec4 color;

vec3 pixel(int x, int y) {
  return texelFetch(picture, ivec2(x, geometry.y - 1 - y), 0).rgb;
}

float luma(vec3 c) {
  return dot(c, vec3(.2126, .7152, .0722));
}

int quantized(float v) {
  return clamp(int(floor(v + .5)), 0, 255);
}

int byteAt(int index) {
  int lumaBytes = geometry.y * geometry.z;
  if (index < lumaBytes) {
    int row = index / geometry.z;
    int col = index - row * geometry.z;
    if (col >= geometry.x) return 0;
    return quantized(16. + 219. * luma(pixel(col, row)));
  }
  int chromaBytes = (geometry.y / 2) * geometry.w;
  int offset = index - lumaBytes;
  int plane = offset / chromaBytes;
  offset -= plane * chromaBytes;
  int row = offset / geometry.w;
  int col = offset - row * geometry.w;
  if (col >= geometry.x / 2) return 0;
  vec3 c = (pixel(2 * col, 2 * row) + pixel(2 * col + 1, 2 * row)
    + pixel(2 * col, 2 * row + 1) + pixel(2 * col + 1, 2 * row + 1)) * .25;
  float y = luma(c);
  float d = plane == 0 ? (c.b - y) / 1.8556 : (c.r - y) / 1.5748;
  return quantized(128. + 224. * d);
}

void main() {
  ivec2 at = ivec2(gl_FragCoord.xy);
  int first = (at.y * (geometry.z / 4) + at.x) * 4;
  color = vec4(float(byteAt(first)), float(byteAt(first + 1)), float(byteAt(first + 2)),
    float(byteAt(first + 3))) / 255.;
}`;

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type) as WebGLShader;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
    throw new Error(gl.getShaderInfoLog(shader) ?? "the I420 shader does not compile");
  return shader;
}

/** The GPU objects of the I420 pass: the picture copied from the canvas, and the texture of packed bytes. */
type Pass = {
  program: WebGLProgram;
  picture: WebGLTexture;
  target: WebGLTexture;
  framebuffer: WebGLFramebuffer;
  texels: { width: number; height: number };
};

/** Reads back the default framebuffer of `gl`, `width` by `height`, after the renderer drew into it. */
export class Readback {
  readonly #gl: WebGL2RenderingContext;
  readonly #message: PixelsMessage;
  readonly #rgba: boolean;
  readonly #width: number;
  readonly #height: number;
  #pass: Pass | null = null;
  /** A still's rows as the framebuffer holds them, bottom row first. */
  #flipped: Uint8Array | null = null;

  constructor(gl: WebGL2RenderingContext, output: OutputWire, width: number, height: number) {
    this.#gl = gl;
    this.#rgba = output === "Rgba";
    this.#width = width;
    this.#height = height;
    this.#message = new PixelsMessage(planesOf(output, width, height));
    gl.pixelStorei(gl.PACK_ALIGNMENT, 1);
    if (this.#rgba) this.#flipped = new Uint8Array(this.#message.layout.bytes);
    else this.#pass = this.#prepare();
  }

  #prepare(): Pass {
    const gl = this.#gl;
    const { stride0, bytes } = this.#message.layout;
    const texels = { width: stride0 / 4, height: bytes / stride0 };
    const limit = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
    if (texels.height > limit || this.#width > limit)
      throw new Error(`This renderer cannot read back pictures this large (up to ${limit} pixels)`);
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS))
      throw new Error(gl.getProgramInfoLog(program) ?? "the I420 shader does not link");
    gl.useProgram(program);
    gl.uniform1i(gl.getUniformLocation(program, "picture"), PICTURE_UNIT);
    gl.uniform4i(
      gl.getUniformLocation(program, "geometry"),
      this.#width,
      this.#height,
      stride0,
      this.#message.layout.stride1,
    );
    const texture = (internal: number, width: number, height: number) => {
      const created = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, created);
      gl.texStorage2D(gl.TEXTURE_2D, 1, internal, width, height);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      return created;
    };
    gl.activeTexture(gl.TEXTURE0 + PICTURE_UNIT);
    const picture = texture(gl.RGB8, this.#width, this.#height);
    const target = texture(gl.RGBA8, texels.width, texels.height);
    const framebuffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, target, 0);
    const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    if (status !== gl.FRAMEBUFFER_COMPLETE) throw new Error("The I420 pass has no usable target");
    return { program, picture, target, framebuffer, texels };
  }

  /** The Pixels message answering Render `seq`, for what the renderer drew last. Valid until the next call. */
  read(seq: number): Uint8Array<ArrayBuffer> {
    const gl = this.#gl;
    const width = this.#width;
    const height = this.#height;
    const pass = this.#pass;
    if (!pass) {
      const rows = this.#flipped as Uint8Array;
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, rows);
      const stride = width * 4;
      for (let row = 0; row < height; row++)
        this.#message.planes.set(
          rows.subarray((height - 1 - row) * stride, (height - row) * stride),
          row * stride,
        );
      return this.#message.answer(seq);
    }
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
    gl.activeTexture(gl.TEXTURE0 + PICTURE_UNIT);
    gl.bindTexture(gl.TEXTURE_2D, pass.picture);
    gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, 0, width, height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, pass.framebuffer);
    gl.viewport(0, 0, pass.texels.width, pass.texels.height);
    gl.useProgram(pass.program);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.readPixels(
      0,
      0,
      pass.texels.width,
      pass.texels.height,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      this.#message.planes,
    );
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return this.#message.answer(seq);
  }

  /** Frees the pass. */
  dispose(): void {
    const gl = this.#gl;
    if (!this.#pass) return;
    gl.deleteProgram(this.#pass.program);
    gl.deleteTexture(this.#pass.picture);
    gl.deleteTexture(this.#pass.target);
    gl.deleteFramebuffer(this.#pass.framebuffer);
    this.#pass = null;
  }
}
