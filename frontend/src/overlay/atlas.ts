/**
 * A renderer's text atlas: glyph bitmaps rasterized once per font on one coverage page, placed by a shelf allocator.
 * Text is laid out glyph by glyph from the cached advances, so a frame of new strings (ids, scores) only costs
 * lookups; strings in scripts that need shaping (joining, combining marks) are rasterized whole instead. Every
 * placement is recorded as an upload the painter copies onto the GPU page (painter.ts).
 *
 * When the page is full the atlas starts a new generation on an empty page; quads laid out on the old generation then
 * sample the wrong glyphs, so every lane lays out again before it is drawn (renderer.ts).
 */

import { AtlasFull } from "../present";

/** Width and height of the page in pixels. */
export const ATLAS_PAGE_SIZE = 2048;

/** Font-wide metrics in device pixels. */
export type FontMetrics = {
  ascent: number;
  descent: number;
  capHeight: number;
  lineHeight: number;
};

/** A text's advance and ink box in device pixels; the box is relative to the baseline origin, up negative. */
export type TextMeasure = {
  advance: number;
  left: number;
  top: number;
  right: number;
  bottom: number;
};

/** Rasterizes text for the atlas: an OffscreenCanvas (canvasGlyphs.ts), a fake in tests. */
export interface GlyphSource {
  metrics(font: string): FontMetrics;
  measure(font: string, text: string): TextMeasure;
  /** Draws `text` in full coverage with its baseline origin at page pixel (x, y). */
  draw(font: string, text: string, x: number, y: number): void;
  /** The coverage of a page rectangle, one byte per pixel, row after row. */
  read(x: number, y: number, width: number, height: number): Uint8Array;
  clear(): void;
}

/** A change to the page, applied to the GPU in the order it happened. */
export type AtlasUpload =
  | { type: "reset"; generation: number }
  | { type: "region"; x: number; y: number; width: number; height: number; alpha: Uint8Array };

/** One bitmap on the page: its advance, its offset from the baseline origin and its page rectangle, in pixels. */
export type Glyph = {
  readonly advance: number;
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
  readonly u: number;
  readonly v: number;
};

/** A font at one device pixel size and weight, with the glyphs placed for it. */
export type AtlasFont = {
  readonly css: string;
  readonly metrics: FontMetrics;
  readonly glyphs: Map<number, Glyph>;
  readonly strings: Map<string, Glyph>;
};

const WEIGHTS = [400, 500, 600, 700];
const DEFAULT_FAMILY = "system-ui, sans-serif";
const MAX_FONTS = 256;
const MAX_STRINGS = 4096;
const NO_GLYPH: Glyph = { advance: 0, left: 0, top: 0, width: 0, height: 0, u: 0, v: 0 };

/** The atlas of one renderer. */
export class TextAtlas {
  readonly #source: GlyphSource;
  readonly #size: number;
  readonly #fonts = new Map<string, Map<number, AtlasFont>>();
  #fontCount = 0;
  #generation = 0;
  #shelf = { x: 0, y: 0, row: 0 };
  #dirty = { left: Infinity, top: Infinity, right: 0, bottom: 0 };
  #uploads: AtlasUpload[] = [];
  /** Leave out glyphs that do not fit instead of throwing `AtlasFull`; set while laying out on a fresh page. */
  dropOverflow = false;
  /** Glyphs left out since the last reset. */
  dropped = 0;

  /** `pageSize` other than the default is for tests. */
  constructor(source: GlyphSource, pageSize = ATLAS_PAGE_SIZE) {
    this.#source = source;
    this.#size = pageSize;
  }

  /** Width and height of the page in pixels. */
  get size(): number {
    return this.#size;
  }

  /** Counts the pages started; layouts remember the one their glyphs are on. */
  get generation(): number {
    return this.#generation;
  }

  /** The font for a family (empty for the default), a size in device pixels and a wire weight code. */
  font(family: string, pixels: number, weight: number): AtlasFont {
    let sizes = this.#fonts.get(family);
    if (!sizes) {
      if (this.#fontCount >= MAX_FONTS) {
        this.#fonts.clear();
        this.#fontCount = 0;
      }
      sizes = new Map();
      this.#fonts.set(family, sizes);
    }
    const key = pixels * 16 + (weight & 3);
    let font = sizes.get(key);
    if (!font) {
      const name = family ? `"${family.replace(/["\\]/g, "")}", ${DEFAULT_FAMILY}` : DEFAULT_FAMILY;
      const css = `${WEIGHTS[weight & 3]} ${pixels}px ${name}`;
      font = { css, metrics: this.#source.metrics(css), glyphs: new Map(), strings: new Map() };
      sizes.set(key, font);
      this.#fontCount++;
    }
    return font;
  }

  /** The glyph of one code point, rasterized on first use. */
  glyph(font: AtlasFont, codePoint: number): Glyph {
    const known = font.glyphs.get(codePoint);
    if (known) return known;
    const placed = this.#place(font, String.fromCodePoint(codePoint));
    font.glyphs.set(codePoint, placed);
    return placed;
  }

  /** A whole line of text as one bitmap, for scripts that cannot be drawn glyph by glyph. */
  whole(font: AtlasFont, text: string): Glyph {
    const known = font.strings.get(text);
    if (known) return known;
    if (font.strings.size >= MAX_STRINGS) font.strings.clear();
    const placed = this.#place(font, text);
    font.strings.set(text, placed);
    return placed;
  }

  /** Starts a new generation on an empty page. */
  reset(): void {
    this.#generation++;
    this.#source.clear();
    for (const sizes of this.#fonts.values())
      for (const font of sizes.values()) {
        font.glyphs.clear();
        font.strings.clear();
      }
    this.#shelf = { x: 0, y: 0, row: 0 };
    this.#dirty = { left: Infinity, top: Infinity, right: 0, bottom: 0 };
    this.#uploads = [{ type: "reset", generation: this.#generation }];
    this.dropped = 0;
  }

  /** The page changes since the last call. */
  takeUploads(): AtlasUpload[] {
    const uploads = this.#uploads;
    const dirty = this.#dirty;
    if (dirty.right > dirty.left) {
      const width = dirty.right - dirty.left;
      const height = dirty.bottom - dirty.top;
      const alpha = this.#source.read(dirty.left, dirty.top, width, height);
      uploads.push({ type: "region", x: dirty.left, y: dirty.top, width, height, alpha });
    }
    this.#uploads = [];
    this.#dirty = { left: Infinity, top: Infinity, right: 0, bottom: 0 };
    return uploads;
  }

  #place(font: AtlasFont, text: string): Glyph {
    const ink = this.#source.measure(font.css, text);
    const left = Math.floor(ink.left);
    const top = Math.floor(ink.top);
    const width = Math.ceil(ink.right) - left;
    const height = Math.ceil(ink.bottom) - top;
    const blank = { ...NO_GLYPH, advance: ink.advance };
    if (!(width > 0 && height > 0)) return blank;
    if (width >= this.#size || height >= this.#size) {
      this.dropped++;
      return blank;
    }
    // Shelves fill left to right and top to bottom, with a pixel between bitmaps so sampling never bleeds.
    let { x, y, row } = this.#shelf;
    if (x + width + 1 > this.#size) {
      x = 0;
      y += row + 1;
      row = 0;
    }
    if (y + height + 1 > this.#size) {
      if (!this.dropOverflow) throw new AtlasFull();
      // Kept as blank: nothing frees room on this page, so it is tried again on the next one.
      this.dropped++;
      return blank;
    }
    this.#shelf = { x: x + width + 1, y, row: Math.max(row, height) };
    this.#source.draw(font.css, text, x - left, y - top);
    const dirty = this.#dirty;
    dirty.left = Math.min(dirty.left, x);
    dirty.top = Math.min(dirty.top, y);
    dirty.right = Math.max(dirty.right, x + width);
    dirty.bottom = Math.max(dirty.bottom, y + height);
    return { advance: ink.advance, left, top, width, height, u: x, v: y };
  }
}
