/** The lanes' presentation on one glyph atlas: each lane's marks laid out for its cell, remembered from frame to frame. */

import type { LaneDrawing } from "../overlay/painter";
import { LanePresenter, type TextSource } from "../present";
import type { LaneFrame } from "./contract";

/** A lane to lay out: what it draws (`undefined`: nothing) and the displayed picture's size in CSS pixels. */
export type PlacedLane = {
  lane: number;
  frame: LaneFrame | undefined;
  view: { width: number; height: number; dpr: number };
};

export class LaneLayouts {
  readonly #text: TextSource;
  readonly #presenters: LanePresenter[] = [];

  constructor(text: TextSource) {
    this.#text = text;
  }

  /** Forgets the lanes from `count` on. */
  keep(count: number): void {
    this.#presenters.length = Math.min(this.#presenters.length, count);
  }

  /**
   * What each placed lane draws, in the order given. A lane whose frame, style and size are as before costs nothing. If a
   * lane filled the atlas page and started a new one, the lanes laid out before it hold glyphs of the old page and are
   * laid out again on the new one, leaving out what does not fit.
   */
  layOut(placed: readonly PlacedLane[]): (LaneDrawing | undefined)[] {
    const generation = this.#text.generation;
    const drawings = placed.map((lane) => this.#present(lane));
    if (this.#text.generation === generation) return drawings;
    this.#text.dropOverflow = true;
    try {
      return placed.map((lane) => this.#present(lane));
    } finally {
      this.#text.dropOverflow = false;
    }
  }

  #present({ lane, frame, view }: PlacedLane): LaneDrawing | undefined {
    if (!frame) return undefined;
    const presenter = (this.#presenters[lane] ??= new LanePresenter(this.#text));
    const quads = presenter.present(
      {
        catalog: frame.catalog,
        sample: frame.columns,
        style: { hidden: frame.hidden, theme: frame.theme },
        mask: frame.classMask,
        filter: frame.filter,
      },
      { ...view, zoom: 1 },
    );
    return { lane, quads, version: presenter.layouts, view, opacity: frame.opacity };
  }
}
