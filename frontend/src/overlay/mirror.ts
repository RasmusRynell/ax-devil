/**
 * The main thread's copy of a viewer's text atlas page. Every prepared drawing's uploads are applied here when it
 * arrives, whether or not it is ever drawn, so the copy matches the worker's page for every drawing of the current
 * generation; the painter copies what changed onto the GPU before drawing, and all of it after a lost context.
 */

import { ATLAS_PAGE_SIZE } from "./atlas";
import type { PreparedDrawing } from "./prepare";

/** One viewer's atlas page as the worker last described it. */
export class AtlasMirror {
  readonly size = ATLAS_PAGE_SIZE;
  readonly pixels = new Uint8Array(ATLAS_PAGE_SIZE * ATLAS_PAGE_SIZE);
  #generation = 0;
  /** The rectangle changed since the painter last took it: left, top, right, bottom. */
  #dirty: [number, number, number, number] | null = null;

  /** The generation the page holds; drawings of any other draw without their texts. */
  get generation(): number {
    return this.#generation;
  }

  /** Applies a drawing's uploads. Call for every drawing in the order they arrive. */
  apply(drawing: Pick<PreparedDrawing, "uploads">): void {
    for (const upload of drawing.uploads) {
      if (upload.type === "reset") {
        this.pixels.fill(0);
        this.#generation = upload.generation;
        this.#dirty = [0, 0, this.size, this.size];
        continue;
      }
      const { x, y, width, height, alpha } = upload;
      if (x + width > this.size || y + height > this.size) continue;
      for (let row = 0; row < height; row++)
        this.pixels.set(alpha.subarray(row * width, (row + 1) * width), (y + row) * this.size + x);
      const dirty = this.#dirty;
      this.#dirty = dirty
        ? [
            Math.min(dirty[0], x),
            Math.min(dirty[1], y),
            Math.max(dirty[2], x + width),
            Math.max(dirty[3], y + height),
          ]
        : [x, y, x + width, y + height];
    }
  }

  /** The rectangle changed since the last call, or `null`. */
  takeDirty(): [number, number, number, number] | null {
    const dirty = this.#dirty;
    this.#dirty = null;
    return dirty;
  }
}
