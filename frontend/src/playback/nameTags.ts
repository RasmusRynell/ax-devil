/**
 * The name tags the export page writes on its lane cells, where the live viewer shows a chip in the DOM. A tag is a
 * dark rounded box with the lane's label in the corner of the cell, laid out like a catalog label on the renderer's
 * glyph atlas and drawn by the same painter.
 */

import type { LaneDrawing } from "../overlay/painter";
import { CodePoints, LaneQuads, QuadSink, type TextSource } from "../present";
import { devicePixels } from "../present/layout/labels";
import { LabelBox } from "../present/text/labelBox";
import { INK, runCacheOf } from "../present/text/runCache";
import { Weight } from "../present/text/source";

/** The tag's smallest font size, and the viewer's, in CSS pixels. Other sizes in CSS pixels too. */
export const NAME_TAG_SIZE = 12;
const PAD_X = 6;
const PAD_Y = 3;
const RADIUS = 4;
const MARGIN = 6;
/** `0xRRGGBBAA`. */
const BACKGROUND = 0x000000b3;
const INK_COLOR = 0xffffffff;

type Tag = {
  quads: LaneQuads;
  version: number;
  label: string;
  size: number;
  dpr: number;
  generation: number;
};

export class NameTags {
  readonly #text: TextSource;
  readonly #tags: Tag[] = [];
  readonly #scratch = new LabelBox();

  constructor(text: TextSource) {
    this.#text = text;
  }

  /**
   * The drawing of tag `lane` for a cell of `width` by `height` CSS pixels, its label set at `size` CSS pixels;
   * `undefined` for an empty label. Laid out again only when the label, the size, the density or the atlas page
   * changed. Glyphs that do not fit the page are left out
   * rather than starting a new page, which would strand the glyphs of the lanes laid out before.
   */
  drawing(
    lane: number,
    label: string,
    dpr: number,
    width: number,
    height: number,
    size: number,
  ): LaneDrawing | undefined {
    const text = this.#text;
    let tag = this.#tags[lane];
    if (
      !tag ||
      tag.label !== label ||
      tag.size !== size ||
      tag.dpr !== dpr ||
      tag.generation !== text.generation
    ) {
      tag = {
        quads: tag?.quads ?? new LaneQuads(),
        version: (tag?.version ?? 0) + 1,
        label,
        size,
        dpr,
        generation: text.generation,
      };
      this.#tags[lane] = tag;
      this.#layOut(tag);
    }
    if (tag.quads.quads.count === 0) return undefined;
    return {
      lane: -1 - lane,
      quads: tag.quads,
      version: tag.version,
      view: { width, height, dpr },
      opacity: 1,
    };
  }

  #layOut({ quads, label, size, dpr }: Tag): void {
    const text = this.#text;
    const sink = new QuadSink(quads);
    sink.begin();
    const points = new CodePoints();
    for (const char of label) points.push(char.codePointAt(0) ?? 0);
    if (points.length > 0) {
      const cache = runCacheOf(text);
      cache.begin(text);
      const font = text.font("", devicePixels(size, dpr), Weight.Semibold);
      text.dropOverflow = true;
      try {
        const entry = cache.run(text, font, cache.fontId(font), points, 0, points.length);
        const { ink } = cache;
        const box = this.#scratch;
        box.clear();
        box.add(
          true,
          ink[entry * INK + 4] / dpr,
          ink[entry * INK] / dpr,
          ink[entry * INK + 1] / dpr,
          ink[entry * INK + 2] / dpr,
          ink[entry * INK + 3] / dpr,
          font.metrics.ascent / dpr,
          font.metrics.descent / dpr,
        );
        if (box.size(PAD_X, PAD_Y, 0, dpr, false) && box.place(MARGIN, MARGIN, 0, 0, dpr)) {
          sink.rect(box.x, box.y, box.width, box.height, RADIUS, BACKGROUND);
          sink.glyphs(
            cache,
            entry,
            Math.round(box.innerX * dpr),
            Math.round(box.baseline * dpr),
            dpr,
            INK_COLOR,
          );
        }
      } finally {
        text.dropOverflow = false;
      }
    }
    sink.endLayer(0);
  }
}
