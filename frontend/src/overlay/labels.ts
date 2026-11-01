/**
 * Texts and labels on the text atlas, ported from the reference's label layout. A label is a background (rounded
 * rectangle), its bars, then its glyphs. Its box is the runs' ink and the font's ascent and descent plus the padding,
 * rounded up to device pixels, and it lands on whole device pixels with its anchor at the point, as do the glyph
 * origins, which keeps the atlas bitmaps crisp. A text is a label of one run with no background and no padding, so
 * its box is its ink alone. Lengths are target CSS pixels.
 */

import type { AtlasFont, Glyph, TextAtlas } from "./atlas";
import { anchorFactor, type DecodedInstructions } from "./instructions";
import { Kind, premultiplied, type QuadWriter } from "./quads";

/** A bar's length and thickness in font sizes, and the opacity of its unfilled track. */
const BAR_LENGTH = 2.5;
const BAR_THICKNESS = 0.3;
const BAR_TRACK_ALPHA = 70;
/** Bars are never thinner than this many target pixels. */
const BAR_MIN = 2;

// Simple scripts are drawn glyph by glyph; anything that joins or combines is rasterized a line at a time.
const SIMPLE =
  /^[\p{Script=Latin}\p{Script=Greek}\p{Script=Cyrillic}\p{Script=Common}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]*$/u;
const COMBINING = /[\p{M}\u200d\p{RI}]/u;
const LINE_BREAKS = /\r\n|[\r\n\u2028\u2029]/;

/** The device pixel ink box and widest line of the last `walkText`. */
const ink = { left: 0, top: 0, right: 0, bottom: 0, advance: 0 };

function isBreak(code: number): boolean {
  return code === 10 || code === 13 || code === 0x2028 || code === 0x2029;
}

/** Where `walkText` writes glyph quads, if anywhere: device pixel origin, color and the target's pixel ratio. */
const sink = { out: null as QuadWriter | null, x: 0, y: 0, color: 0, dpr: 1 };

function place(glyphLeft: number, glyphTop: number, glyph: Glyph): void {
  if (glyph.width <= 0) return;
  ink.left = Math.min(ink.left, glyphLeft);
  ink.top = Math.min(ink.top, glyphTop);
  ink.right = Math.max(ink.right, glyphLeft + glyph.width);
  ink.bottom = Math.max(ink.bottom, glyphTop + glyph.height);
  const { out, dpr } = sink;
  if (out)
    out.push(
      Kind.glyph,
      (sink.x + glyphLeft) / dpr,
      (sink.y + glyphTop) / dpr,
      1,
      0,
      glyph.width / dpr,
      glyph.height / dpr,
      glyph.u,
      glyph.v,
      0,
      sink.color,
      0,
    );
}

/**
 * Lays out `text` from the baseline origin (0, 0) in device pixels, line under line, into `ink`, and writes its
 * glyph quads to `sink.out` when set.
 */
function walkText(atlas: TextAtlas, font: AtlasFont, text: string): void {
  ink.left = Infinity;
  ink.top = Infinity;
  ink.right = -Infinity;
  ink.bottom = -Infinity;
  ink.advance = 0;
  if (SIMPLE.test(text) && !COMBINING.test(text)) {
    let line = 0;
    let pen = 0;
    for (let index = 0; index < text.length;) {
      const code = text.codePointAt(index) ?? 0;
      index += code > 0xffff ? 2 : 1;
      if (isBreak(code)) {
        if (code === 13 && text.charCodeAt(index) === 10) index++;
        ink.advance = Math.max(ink.advance, pen);
        pen = 0;
        line++;
        continue;
      }
      const glyph = atlas.glyph(font, code);
      place(
        Math.round(pen) + glyph.left,
        Math.round(line * font.metrics.lineHeight) + glyph.top,
        glyph,
      );
      pen += glyph.advance;
    }
    ink.advance = Math.max(ink.advance, pen);
  } else {
    text.split(LINE_BREAKS).forEach((part, line) => {
      if (!part) return;
      const glyph = atlas.whole(font, part);
      place(glyph.left, Math.round(line * font.metrics.lineHeight) + glyph.top, glyph);
      ink.advance = Math.max(ink.advance, glyph.advance);
    });
  }
  if (ink.right < ink.left) {
    ink.left = 0;
    ink.top = 0;
    ink.right = 0;
    ink.bottom = 0;
  }
}

/**
 * Where a label goes: fed its runs' sizes, it finds its box and its anchored, pixel-snapped position. One instance
 * is reused for every label of a frame.
 */
export class LabelLayout {
  /** Per run: advance, ink left, top, right, bottom, and offset from the first run, in target pixels. */
  runs = new Float64Array(6 * 8);
  /** How many runs were added since `clear`, shown or not. */
  count = 0;
  #shown = 0;
  #ascent = 0;
  #descent = 0;
  /** Results of `place`: the box's top-left corner and size, and the first run's baseline origin. */
  x = 0;
  y = 0;
  width = 0;
  height = 0;
  innerX = 0;
  baseline = 0;

  clear(): void {
    this.count = 0;
    this.#shown = 0;
    this.#ascent = 0;
    this.#descent = 0;
  }

  /** Adds a run; a run that is not shown takes no space. Ink is relative to the run's baseline origin. */
  add(
    shown: boolean,
    advance: number,
    left: number,
    top: number,
    right: number,
    bottom: number,
    ascent: number,
    descent: number,
  ): void {
    if (this.runs.length < (this.count + 1) * 6) {
      const grown = new Float64Array(this.runs.length * 2);
      grown.set(this.runs);
      this.runs = grown;
    }
    const runs = this.runs;
    const at = this.count * 6;
    runs[at] = shown ? advance : Number.NaN;
    runs[at + 1] = left;
    runs[at + 2] = top;
    runs[at + 3] = right;
    runs[at + 4] = bottom;
    this.count++;
    if (!shown) return;
    this.#shown++;
    this.#ascent = Math.max(this.#ascent, ascent);
    this.#descent = Math.max(this.#descent, descent);
  }

  /** Whether the run was shown. */
  shown(run: number): boolean {
    return !Number.isNaN(this.runs[run * 6]);
  }

  /** The run's offset from the first run's origin, after `place`. */
  offset(run: number): number {
    return this.runs[run * 6 + 5];
  }

  /**
   * Places the label with its `anchor` at target point (`px`, `py`). `plain` labels (texts) have no background, so
   * their box is the ink alone. Returns whether the label is drawn: it has a shown run and a finite, positive size.
   */
  place(
    px: number,
    py: number,
    anchor: number,
    padX: number,
    padY: number,
    gap: number,
    dpr: number,
    plain: boolean,
  ): boolean {
    if (this.#shown === 0) return false;
    const runs = this.runs;
    let running = 0;
    for (let run = 0; run < this.count; run++) {
      runs[run * 6 + 5] = running;
      if (this.shown(run)) running += runs[run * 6] + gap;
    }
    const ascent = plain ? 0 : this.#ascent;
    let left = 0;
    let right = running - gap;
    let top = -ascent;
    let bottom = plain ? 0 : this.#descent;
    for (let run = 0; run < this.count; run++) {
      if (!this.shown(run)) continue;
      const at = run * 6;
      left = Math.min(left, runs[at + 5] + runs[at + 1]);
      top = Math.min(top, runs[at + 2]);
      right = Math.max(right, runs[at + 5] + runs[at + 3]);
      bottom = Math.max(bottom, runs[at + 4]);
    }
    // Device pixels covered, rounded up; a hair of tolerance keeps float noise from adding a pixel.
    const width = Math.ceil((right - left + 2 * padX) * dpr - 1e-3) / dpr;
    const height = Math.ceil((bottom - top + 2 * padY) * dpr - 1e-3) / dpr;
    const exactX = px - anchorFactor(anchor, 0) * width;
    const exactY = py - anchorFactor(anchor, 1) * height;
    if (!(width > 0 && height > 0 && width * dpr < 2 ** 24 && height * dpr < 2 ** 24)) return false;
    if (!Number.isFinite(exactX) || !Number.isFinite(exactY)) return false;
    this.x = Math.round(exactX * dpr) / dpr;
    this.y = Math.round(exactY * dpr) / dpr;
    this.width = width;
    this.height = height;
    this.innerX = this.x + padX - left;
    this.baseline = this.y + padY - top;
    return true;
  }
}

/** The target and atlas a drawing is prepared for. */
export type TextTarget = {
  width: number;
  height: number;
  /** min(width, height): what instruction lengths are relative to. */
  scale: number;
  dpr: number;
  atlas: TextAtlas;
};

const layout = new LabelLayout();
/** The font of each run of the label being laid out. */
const fonts: AtlasFont[] = [];

/** A font size in device pixels, snapped to halves so sizes that follow object sizes share a few fonts. */
function devicePixels(size: number, dpr: number): number {
  return Math.max(0.5, Math.round(size * dpr * 2) / 2);
}

function addTextRun(target: TextTarget, font: AtlasFont, text: string, plain: boolean): void {
  sink.out = null;
  walkText(target.atlas, font, text);
  const { dpr } = target;
  const metrics = font.metrics;
  layout.add(
    text !== "",
    ink.advance / dpr,
    ink.left / dpr,
    ink.top / dpr,
    ink.right / dpr,
    ink.bottom / dpr,
    plain ? 0 : metrics.ascent / dpr,
    plain ? 0 : metrics.descent / dpr,
  );
}

/** Writes the glyph quads of a shown text run placed by `layout`. */
function emitRun(
  target: TextTarget,
  run: number,
  font: AtlasFont,
  text: string,
  color: number,
  out: QuadWriter,
): void {
  const { dpr } = target;
  sink.out = out;
  sink.x = Math.round((layout.innerX + layout.offset(run)) * dpr);
  sink.y = Math.round(layout.baseline * dpr);
  sink.color = color;
  sink.dpr = dpr;
  walkText(target.atlas, font, text);
  sink.out = null;
}

/** Writes the glyph quads of every text row. */
export function drawTexts(d: DecodedInstructions, target: TextTarget, out: QuadWriter): void {
  const { f32, u32, u8, count } = d.texts;
  const paints = d.paints;
  for (let row = 0; row < count; row++) {
    const at = row * 5;
    const paint = u32[at + 4];
    const text = d.strings[u32[at + 2]] ?? "";
    if (paint >= paints.count || text === "") continue;
    const p = paint * 6;
    const pixels = devicePixels(paints.f32[p + 4] * target.scale, target.dpr);
    if (!Number.isFinite(pixels)) continue;
    const font = target.atlas.font(d.strings[paints.u32[p + 3]] ?? "", pixels, 0);
    layout.clear();
    addTextRun(target, font, text, true);
    const anchor = u8[(at + 3) * 4];
    const x = f32[at] * target.width;
    const y = f32[at + 1] * target.height;
    if (!layout.place(x, y, anchor, 0, 0, 0, target.dpr, true)) continue;
    const pb = p * 4;
    const color = premultiplied(paints.u8[pb], paints.u8[pb + 1], paints.u8[pb + 2], 255);
    emitRun(target, 0, font, text, color, out);
  }
}

/** Writes the quads of every label: background, bars, then glyphs, label after label. */
export function drawLabels(d: DecodedInstructions, target: TextTarget, out: QuadWriter): void {
  const { f32, u32, u8, count } = d.labels;
  const styles = d.labelStyles;
  const runs = d.runs;
  const { dpr, scale, atlas } = target;
  for (let row = 0; row < count; row++) {
    const at = row * 6;
    const style = u32[at + 3];
    const first = u32[at + 4];
    const length = u32[at + 5];
    if (style >= styles.count || first + length > runs.count) continue;
    const s = style * 7;
    const size = styles.f32[s] * scale;
    if (!(size > 0 && size < Infinity)) continue;
    const family = d.strings[styles.u32[s + 1]] ?? "";
    const pixels = devicePixels(size, dpr);
    layout.clear();
    for (let run = 0; run < length; run++) {
      const r = (first + run) * 3;
      const runFont = atlas.font(family, pixels, runs.u8[(r + 2) * 4 + 3]);
      fonts[run] = runFont;
      const bar = runs.f32[r + 1];
      if (Number.isNaN(bar)) {
        addTextRun(target, runFont, d.strings[runs.u32[r]] ?? "", false);
        continue;
      }
      const middle = -runFont.metrics.capHeight / dpr / 2;
      const half = Math.max(BAR_MIN, size * BAR_THICKNESS) / 2;
      const { ascent, descent } = runFont.metrics;
      layout.add(
        true,
        size * BAR_LENGTH,
        0,
        middle - half,
        size * BAR_LENGTH,
        middle + half,
        ascent / dpr,
        descent / dpr,
      );
    }
    const padX = styles.f32[s + 3] * scale;
    const padY = styles.f32[s + 4] * scale;
    const gap = styles.f32[s + 6] * scale;
    if (
      !layout.place(
        f32[at] * target.width,
        f32[at + 1] * target.height,
        u8[(at + 2) * 4],
        padX,
        padY,
        gap,
        dpr,
        false,
      )
    )
      continue;
    const bg = (s + 2) * 4;
    if (styles.u8[bg + 3] > 0) {
      const radius = Math.min(styles.f32[s + 5] * scale, Math.min(layout.width, layout.height) / 2);
      const fill = premultiplied(
        styles.u8[bg],
        styles.u8[bg + 1],
        styles.u8[bg + 2],
        styles.u8[bg + 3],
      );
      out.rect(layout.x, layout.y, layout.width, layout.height, Math.max(0, radius) || 0, fill);
    }
    for (let run = 0; run < length; run++) {
      const r = (first + run) * 3;
      const bar = runs.f32[r + 1];
      if (Number.isNaN(bar) || !layout.shown(run)) continue;
      const c = (r + 2) * 4;
      const thickness = Math.max(BAR_MIN, size * BAR_THICKNESS);
      const left = layout.innerX + layout.offset(run);
      const top = layout.baseline - fonts[run].metrics.capHeight / dpr / 2 - thickness / 2;
      const barLength = size * BAR_LENGTH;
      const red = runs.u8[c];
      const green = runs.u8[c + 1];
      const blue = runs.u8[c + 2];
      out.rect(
        left,
        top,
        barLength,
        thickness,
        thickness / 2,
        premultiplied(red, green, blue, BAR_TRACK_ALPHA),
      );
      const filled = Math.min(1, Math.max(0, bar));
      if (filled > 0) {
        const width = Math.max(thickness, barLength * filled);
        out.rect(left, top, width, thickness, thickness / 2, premultiplied(red, green, blue, 255));
      }
    }
    for (let run = 0; run < length; run++) {
      const r = (first + run) * 3;
      if (!Number.isNaN(runs.f32[r + 1]) || !layout.shown(run)) continue;
      const c = (r + 2) * 4;
      const color = premultiplied(runs.u8[c], runs.u8[c + 1], runs.u8[c + 2], 255);
      emitRun(target, run, fonts[run], d.strings[runs.u32[r]] ?? "", color, out);
    }
  }
}
