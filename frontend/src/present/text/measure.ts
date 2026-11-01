import type { CodePoints } from "../expr/codePoints";
import type { FontHandle, Glyph, TextSource } from "./source";

// Scripts drawn glyph by glyph; anything that joins or combines is rasterized a line at a time.
const SIMPLE =
  /^[\p{Script=Latin}\p{Script=Greek}\p{Script=Cyrillic}\p{Script=Common}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]$/u;
const COMBINING = /[\p{M}‍\p{RI}]/u;

/** 1 simple, 2 needs whole-line shaping, 0 not looked at yet; the common range is a table, the rest a map. */
const verdicts = new Uint8Array(0x800);
const later = new Map<number, boolean>();

function simple(codePoint: number): boolean {
  if (codePoint < verdicts.length) {
    let verdict = verdicts[codePoint];
    if (verdict === 0) {
      const char = String.fromCodePoint(codePoint);
      verdict = SIMPLE.test(char) && !COMBINING.test(char) ? 1 : 2;
      verdicts[codePoint] = verdict;
    }
    return verdict === 1;
  }
  let known = later.get(codePoint);
  if (known === undefined) {
    const char = String.fromCodePoint(codePoint);
    known = SIMPLE.test(char) && !COMBINING.test(char);
    later.set(codePoint, known);
  }
  return known;
}

const TABLE = 0x800;

const isBreak = (code: number): boolean =>
  code === 10 || code === 13 || code === 0x2028 || code === 0x2029;

/**
 * The glyphs of the runs measured so far, with where each goes relative to its run's baseline origin (device
 * pixels), so the quads are written without looking the glyphs up again. Reused: `clear` empties it.
 */
export class GlyphScratch {
  glyphs: Glyph[] = [];
  /** Per glyph, the position of its bitmap's top-left corner. */
  x = new Float64Array(256);
  y = new Float64Array(256);
  count = 0;
  /** Glyphs by code point below `TABLE` per font, so the common ones cost an index instead of a map lookup. */
  private readonly tables = new Map<FontHandle, (Glyph | undefined)[]>();
  private generation = -1;
  /** The device-pixel ink box and widest line of the last `measureRun`, from its baseline origin. */
  left = 0;
  top = 0;
  right = 0;
  bottom = 0;
  advance = 0;

  clear(): void {
    this.count = 0;
  }

  private glyph(text: TextSource, font: FontHandle, code: number): Glyph {
    if (code >= TABLE) return text.glyph(font, code);
    let table = this.tables.get(font);
    if (!table) {
      table = [];
      this.tables.set(font, table);
    }
    return (table[code] ??= text.glyph(font, code));
  }

  private add(x: number, y: number, glyph: Glyph): void {
    const width = glyph.width;
    if (width <= 0) return;
    if (x < this.left) this.left = x;
    if (y < this.top) this.top = y;
    if (x + width > this.right) this.right = x + width;
    if (y + glyph.height > this.bottom) this.bottom = y + glyph.height;
    if (this.count === this.x.length) {
      const grownX = new Float64Array(this.count * 2);
      grownX.set(this.x);
      this.x = grownX;
      const grownY = new Float64Array(this.count * 2);
      grownY.set(this.y);
      this.y = grownY;
    }
    this.glyphs[this.count] = glyph;
    this.x[this.count] = x;
    this.y[this.count++] = y;
  }

  /**
   * Lay out code points `start` to `end` from the baseline origin (0, 0), line under line, appending their glyphs
   * and setting the ink box and advance of this run alone.
   */
  measureRun(
    text: TextSource,
    font: FontHandle,
    cps: CodePoints,
    start: number,
    end: number,
  ): void {
    if (text.generation !== this.generation) {
      this.tables.clear();
      this.generation = text.generation;
    }
    this.left = Infinity;
    this.top = Infinity;
    this.right = -Infinity;
    this.bottom = -Infinity;
    this.advance = 0;
    const data = cps.data;
    let plain = true;
    for (let i = start; plain && i < end; i++) plain = simple(data[i]);
    const lineHeight = font.metrics.lineHeight;
    let line = 0;
    if (plain) {
      let lineTop = 0;
      let pen = 0;
      for (let i = start; i < end; i++) {
        const code = data[i];
        if (code < 14 || code >= 0x2028) {
          if (isBreak(code)) {
            if (code === 13 && data[i + 1] === 10 && i + 1 < end) i++;
            this.advance = Math.max(this.advance, pen);
            pen = 0;
            line++;
            lineTop = Math.round(line * lineHeight);
            continue;
          }
        }
        const glyph = this.glyph(text, font, code);
        this.add(Math.round(pen) + glyph.left, lineTop + glyph.top, glyph);
        pen += glyph.advance;
      }
      this.advance = Math.max(this.advance, pen);
    } else {
      let from = start;
      for (let i = start; i <= end; i++) {
        if (i < end && !isBreak(data[i])) continue;
        if (i > from) {
          const glyph = text.whole(font, cps, from, i);
          this.add(glyph.left, Math.round(line * lineHeight) + glyph.top, glyph);
          this.advance = Math.max(this.advance, glyph.advance);
        }
        if (i < end && data[i] === 13 && data[i + 1] === 10 && i + 1 < end) i++;
        from = i + 1;
        line++;
      }
    }
    if (this.right < this.left) {
      this.left = 0;
      this.top = 0;
      this.right = 0;
      this.bottom = 0;
    }
  }
}
