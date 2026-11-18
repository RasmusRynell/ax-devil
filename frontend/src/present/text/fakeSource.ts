import type { CodePoints } from "../expr/codePoints";
import type { FontHandle, Glyph, TextSource } from "./source";

/**
 * A `TextSource` with the metrics the oracle comparison assumes: ascent 0.928 em, descent 0.236 em, cap height
 * 0.73 em, every glyph 0.6 em wide. Glyphs are made on first use and kept per font in a map, like the real atlas, so
 * looking one up costs about what it does there. For tests and benchmarks only.
 */
export class FakeText implements TextSource {
  generation = 0;
  dropOverflow = false;
  private readonly fonts = new Map<string, { handle: FontHandle; glyphs: Map<number, Glyph> }>();
  private readonly owner = new Map<FontHandle, Map<number, Glyph>>();
  /** Glyphs made since construction, to see what a frame had to rasterize. */
  made = 0;

  font(family: string, devicePixels: number, weight: number): FontHandle {
    const key = `${family}/${devicePixels}/${weight}`;
    let font = this.fonts.get(key);
    if (!font) {
      const em = devicePixels;
      const handle = {
        metrics: {
          ascent: 0.928 * em,
          descent: 0.236 * em,
          capHeight: 0.73 * em,
          lineHeight: 1.164 * em,
        },
      };
      font = { handle, glyphs: new Map() };
      this.fonts.set(key, font);
      this.owner.set(handle, font.glyphs);
    }
    return font.handle;
  }

  glyph(font: FontHandle, codePoint: number): Glyph {
    const glyphs = this.owner.get(font) as Map<number, Glyph>;
    let glyph = glyphs.get(codePoint);
    if (!glyph) {
      glyph = this.make(font, codePoint === 32 ? 0 : 1);
      glyphs.set(codePoint, glyph);
    }
    return glyph;
  }

  whole(font: FontHandle, text: CodePoints, start: number, end: number): Glyph {
    return this.make(font, end - start);
  }

  reset(): void {
    this.generation++;
    for (const glyphs of this.owner.values()) glyphs.clear();
  }

  private make(font: FontHandle, count: number): Glyph {
    this.made++;
    const em = font.metrics.ascent / 0.928;
    const advance = 0.6 * em * Math.max(count, 1);
    const ink = count > 0;
    // Bitmaps are whole pixels, as the atlas rasterizes them; only the advance is fractional.
    return {
      advance,
      left: 0,
      top: ink ? -Math.ceil(font.metrics.capHeight) : 0,
      width: ink ? Math.ceil(advance) : 0,
      height: ink ? Math.ceil(font.metrics.capHeight) : 0,
      u: 0,
      v: 0,
    };
  }
}
