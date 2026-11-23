/**
 * The offscreen layer a faded lane is drawn into. The lane's marks draw at full strength into the layer, which is then
 * blended onto the canvas once at the lane's opacity, so overlapping marks fade as one picture instead of darkening
 * where they overlap. Its stencil is the one the polygon fills use.
 */

import { COMPOSITE_FRAGMENT, COMPOSITE_VERTEX, compileProgram } from "./shaders";

/** The texture unit of the layer; the video planes use 0 to 2, the glyph atlas 3 and the I420 pass 4. */
const LAYER_UNIT = 5;

type Target = { framebuffer: WebGLFramebuffer; color: WebGLTexture; stencil: WebGLRenderbuffer };

/** The fade layer of one WebGL2 context, created on the first faded draw and as large as the drawing buffer. */
export class FadeLayer {
  #target: (Target & { width: number; height: number }) | undefined;
  #composite: { program: WebGLProgram; opacity: WebGLUniformLocation | null } | undefined;

  /**
   * Makes the layer and its blending program ready, so `begin` and `composite` cannot fail. Throws when the context
   * cannot render into a layer; the framebuffer binding and the render state are left as they were.
   */
  prepare(gl: WebGL2RenderingContext): void {
    const width = gl.drawingBufferWidth;
    const height = gl.drawingBufferHeight;
    if (!this.#composite) {
      const program = compileProgram(gl, COMPOSITE_VERTEX, COMPOSITE_FRAGMENT);
      gl.useProgram(program);
      gl.uniform1i(gl.getUniformLocation(program, "layer"), LAYER_UNIT);
      this.#composite = { program, opacity: gl.getUniformLocation(program, "opacity") };
    }
    if (this.#target?.width !== width || this.#target.height !== height) {
      this.#freeTarget(gl);
      this.#target = { ...create(gl, width, height), width, height };
    }
  }

  /** Binds the layer, cleared inside the current scissor box, for the lane's marks. Call `prepare` first. */
  begin(gl: WebGL2RenderingContext): void {
    if (!this.#target) return;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.#target.framebuffer);
    gl.clearColor(0, 0, 0, 0);
    gl.clearStencil(0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT);
  }

  /** Blends the layer onto the canvas inside the current scissor box at `opacity`, and leaves the canvas bound. */
  composite(gl: WebGL2RenderingContext, opacity: number): void {
    if (!this.#target || !this.#composite) return;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.activeTexture(gl.TEXTURE0 + LAYER_UNIT);
    gl.bindTexture(gl.TEXTURE_2D, this.#target.color);
    gl.useProgram(this.#composite.program);
    gl.uniform1f(this.#composite.opacity, opacity);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  /** Frees the layer and its program. */
  dispose(gl: WebGL2RenderingContext): void {
    this.#freeTarget(gl);
    if (this.#composite) gl.deleteProgram(this.#composite.program);
    this.#composite = undefined;
  }

  #freeTarget(gl: WebGL2RenderingContext): void {
    if (!this.#target) return;
    gl.deleteFramebuffer(this.#target.framebuffer);
    gl.deleteTexture(this.#target.color);
    gl.deleteRenderbuffer(this.#target.stencil);
    this.#target = undefined;
  }
}

function create(gl: WebGL2RenderingContext, width: number, height: number): Target {
  const color = gl.createTexture();
  const stencil = gl.createRenderbuffer();
  const framebuffer = gl.createFramebuffer();
  try {
    // Under resource pressure an allocation returns null, and binding null selects the canvas instead.
    if (!color || !stencil || !framebuffer)
      throw new Error("The overlay fade layer cannot be allocated");
    gl.activeTexture(gl.TEXTURE0 + LAYER_UNIT);
    gl.bindTexture(gl.TEXTURE_2D, color);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, width, height);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.bindRenderbuffer(gl.RENDERBUFFER, stencil);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH24_STENCIL8, width, height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, color, 0);
    gl.framebufferRenderbuffer(
      gl.FRAMEBUFFER,
      gl.DEPTH_STENCIL_ATTACHMENT,
      gl.RENDERBUFFER,
      stencil,
    );
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
      throw new Error("The overlay fade layer has no usable target");
    return { framebuffer, color, stencil };
  } catch (error) {
    gl.deleteFramebuffer(framebuffer);
    gl.deleteTexture(color);
    gl.deleteRenderbuffer(stencil);
    throw error;
  } finally {
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }
}
