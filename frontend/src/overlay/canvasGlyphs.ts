/**
 * The text atlas's rasterizer in the playback worker: an OffscreenCanvas 2D page kept in memory, so the browser's
 * own text rendering draws the glyphs at the screen's pixel density and reading coverage back is a copy.
 */

import { ATLAS_PAGE_SIZE, type FontMetrics, type GlyphSource, type TextMeasure } from "./atlas";

/** Where text cannot be rasterized (no OffscreenCanvas 2D), it takes no space and draws nothing. */
const BLANK: GlyphSource = {
  metrics: () => ({ ascent: 0, descent: 0, capHeight: 0, lineHeight: 0 }),
  measure: () => ({ advance: 0, left: 0, top: 0, right: 0, bottom: 0 }),
  draw: () => undefined,
  read: (_x, _y, width, height) => new Uint8Array(width * height),
  clear: () => undefined,
};

/** A glyph source over an OffscreenCanvas page of `size` pixels. */
export function canvasGlyphs(size = ATLAS_PAGE_SIZE): GlyphSource {
  if (typeof OffscreenCanvas === "undefined") return BLANK;
  const canvas = new OffscreenCanvas(size, size);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return BLANK;
  context.textBaseline = "alphabetic";
  context.textAlign = "left";
  context.fillStyle = "#fff";
  let current = "";
  const use = (font: string) => {
    if (font !== current) context.font = font;
    current = font;
  };
  return {
    metrics(font: string): FontMetrics {
      use(font);
      const metrics = context.measureText("H");
      const ascent = metrics.fontBoundingBoxAscent;
      const descent = metrics.fontBoundingBoxDescent;
      return {
        ascent,
        descent,
        capHeight: metrics.actualBoundingBoxAscent,
        lineHeight: ascent + descent,
      };
    },
    measure(font: string, text: string): TextMeasure {
      use(font);
      const metrics = context.measureText(text);
      return {
        advance: metrics.width,
        left: -metrics.actualBoundingBoxLeft,
        top: -metrics.actualBoundingBoxAscent,
        right: metrics.actualBoundingBoxRight,
        bottom: metrics.actualBoundingBoxDescent,
      };
    },
    draw(font: string, text: string, x: number, y: number): void {
      use(font);
      context.fillText(text, x, y);
    },
    read(x: number, y: number, width: number, height: number): Uint8Array {
      const rgba = context.getImageData(x, y, width, height).data;
      const alpha = new Uint8Array(width * height);
      for (let index = 0; index < alpha.length; index++) alpha[index] = rgba[index * 4 + 3];
      return alpha;
    },
    clear(): void {
      context.clearRect(0, 0, size, size);
    },
  };
}
