/**
 * Draws decoded frames on a canvas with WebGL2. Planar and semi-planar YUV frames are converted to RGB in a shader
 * from the matrix and range in the frame header; RGBA frames are drawn as they are. The picture is fitted into the
 * canvas keeping its aspect, on a near-black field.
 */

import type { Matrix, VideoFrame } from "./protocol";

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

export class VideoRenderer {
  readonly #canvas: HTMLCanvasElement;
  readonly #gl: WebGL2RenderingContext;
  readonly #program: WebGLProgram;
  readonly #uniforms: Record<string, WebGLUniformLocation | null> = {};
  #planes: Planes | undefined;
  #picture: { width: number; height: number } | undefined;

  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
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
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  }

  /** Sets the canvas to this many device pixels and draws the picture again. */
  resize(width: number, height: number): void {
    if (this.#canvas.width !== width) this.#canvas.width = width;
    if (this.#canvas.height !== height) this.#canvas.height = height;
    this.redraw();
  }

  /** Uploads the frame and draws it. */
  present(frame: VideoFrame): void {
    const gl = this.#gl;
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
    this.redraw();
  }

  /** Draws the last uploaded frame again, fitted to the canvas as it is now. */
  redraw(): void {
    const gl = this.#gl;
    const { width: canvasWidth, height: canvasHeight } = this.#canvas;
    gl.viewport(0, 0, canvasWidth, canvasHeight);
    gl.clearColor(FIELD, FIELD, FIELD, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (!this.#picture || !this.#planes) return;
    const scale = Math.min(canvasWidth / this.#picture.width, canvasHeight / this.#picture.height);
    const width = Math.max(1, Math.floor(this.#picture.width * scale));
    const height = Math.max(1, Math.floor(this.#picture.height * scale));
    gl.viewport(
      Math.floor((canvasWidth - width) / 2),
      Math.floor((canvasHeight - height) / 2),
      width,
      height,
    );
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  /** Frees the GPU's copy of the picture. */
  dispose(): void {
    this.#dropPlanes();
    this.#gl.deleteProgram(this.#program);
  }

  #dropPlanes(): void {
    this.#planes?.textures.forEach((texture) => this.#gl.deleteTexture(texture));
    this.#planes = undefined;
  }
}
