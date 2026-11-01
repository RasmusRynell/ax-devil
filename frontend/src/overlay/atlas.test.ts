// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { AtlasFull } from "../present";
import { type GlyphSource, TextAtlas } from "./atlas";

// A 32 px page holds twelve 10 px glyphs (6 by 9 each, a pixel apart): four on each of three shelves.
const PAGE = 32;

/** Every character advances 0.6 of the font size and inks 0.7 above to 0.2 below the baseline. */
function fakeGlyphs(): GlyphSource & { drawn: string[] } {
  const size = (font: string) => Number(/([\d.]+)px/.exec(font)?.[1] ?? 0);
  const drawn: string[] = [];
  return {
    drawn,
    metrics: (font) => ({
      ascent: size(font),
      descent: 0,
      capHeight: size(font),
      lineHeight: size(font),
    }),
    measure: (font, text) => {
      const advance = text.length * 0.6 * size(font);
      return { advance, left: 0, top: -0.7 * size(font), right: advance, bottom: 0.2 * size(font) };
    },
    draw: (_font, text) => void drawn.push(text),
    read: (_x, _y, width, height) => new Uint8Array(width * height).fill(255),
    clear: () => undefined,
  };
}

const code = (letter: string) => letter.codePointAt(0) as number;

describe("TextAtlas", () => {
  it("rasterizes each glyph once and uploads only the new ones", () => {
    const source = fakeGlyphs();
    const atlas = new TextAtlas(source, PAGE);
    const font = atlas.font("", 10, 0);
    for (const letter of "abab") atlas.glyph(font, code(letter));
    expect(source.drawn).toEqual(["a", "b"]);
    expect(atlas.takeUploads()).toEqual([
      { type: "region", x: 0, y: 0, width: 13, height: 9, alpha: expect.any(Uint8Array) },
    ]);
    atlas.glyph(font, code("b"));
    expect(atlas.takeUploads()).toEqual([]);
  });

  it("starts a new page when one fills up, or leaves out what does not fit while told to", () => {
    const atlas = new TextAtlas(fakeGlyphs(), PAGE);
    const font = atlas.font("", 10, 0);
    for (const letter of "abcdefghijkl") atlas.glyph(font, code(letter));
    expect(() => atlas.glyph(font, code("m"))).toThrow(AtlasFull);

    atlas.reset();
    expect(atlas.generation).toBe(1);
    expect(atlas.takeUploads()).toEqual([{ type: "reset", generation: 1 }]);
    for (const letter of "abcdefghijkl") atlas.glyph(font, code(letter));
    atlas.dropOverflow = true;
    expect(atlas.glyph(font, code("m")).width).toBe(0);
    expect(atlas.dropped).toBe(1);
  });
});
