import type { CodePoints } from "../expr/codePoints";
import { GlyphScratch } from "./measure";
import type { FontHandle, TextSource } from "./source";

/** Numbers per cached run in `RunCache.ink`: ink left, top, right, bottom and advance, in device pixels. */
export const INK = 5;
/** Numbers per cached glyph in `RunCache.glyphs`: x, y of its bitmap relative to the run origin, width, height, u, v. */
export const GLYPH = 6;

/** Past these a cache is emptied before the next layout, so it stays a few tens of megabytes however long the session runs. */
const MAX_RUNS = 1 << 16;
const MAX_GLYPHS = 1 << 19;
/** Longest run laid out; the rest of a longer text is left off, so one mark cannot outgrow the cache or the page. */
export const MAX_RUN_CODE_POINTS = 4096;

/**
 * Laid-out runs by (font, code points): the ink box, advance and the glyph quads relative to the run origin, so a
 * label whose id, score or class was seen before costs a hash and a copy instead of a glyph lookup per code point. One
 * per text source, shared by its lanes, and emptied when the source resets its page (every glyph in it is stale).
 */
export class RunCache {
  /** Ink box and advance of run `entry` at `entry * INK`. */
  ink = new Float64Array(INK * 256);
  /** The glyphs of a run, `GLYPH` numbers each. */
  glyphs = new Float64Array(GLYPH * 4096);
  /** Per run: its first glyph and glyph count. */
  glyphFirst = new Uint32Array(256);
  glyphEnd = new Uint32Array(256);
  private hash = new Uint32Array(256);
  private font = new Int32Array(256);
  private cpFirst = new Uint32Array(256);
  private cpEnd = new Uint32Array(256);
  private cps = new Uint32Array(2048);
  private cpLength = 0;
  private table = new Int32Array(512).fill(-1);
  private runs = 0;
  private glyphCount = 0;
  private generation = -1;
  private readonly fontIds = new Map<FontHandle, number>();
  /** Counts the times the cache was emptied; numbers it handed out (`fontId`) are good while this stays. */
  epoch = 0;
  private readonly scratch = new GlyphScratch();

  /** Start a layout pass on `text`: forget everything if the page was reset or the cache grew past its bounds. */
  begin(text: TextSource): void {
    if (text.generation === this.generation && this.runs < MAX_RUNS && this.glyphCount < MAX_GLYPHS)
      return;
    this.generation = text.generation;
    this.epoch++;
    this.table.fill(-1);
    this.runs = 0;
    this.glyphCount = 0;
    this.cpLength = 0;
    this.fontIds.clear();
  }

  /** Forget everything at the next `begin`: runs measured on a page that drops glyphs are missing some. */
  discard(): void {
    this.generation = -1;
  }

  /** The number that stands for `font` in `run`; valid until the next reset of the page. */
  fontId(font: FontHandle): number {
    let id = this.fontIds.get(font);
    if (id === undefined) {
      id = this.fontIds.size;
      this.fontIds.set(font, id);
    }
    return id;
  }

  /** The run of code points `start` to `end` (at most `MAX_RUN_CODE_POINTS`) in `font` (named `fontId`), measured on the first ask. */
  run(
    text: TextSource,
    font: FontHandle,
    fontId: number,
    cps: CodePoints,
    start: number,
    runEnd: number,
  ): number {
    const end = Math.min(runEnd, start + MAX_RUN_CODE_POINTS);
    const data = cps.data;
    let hash = Math.imul(fontId + 1, 0x9e3779b1) ^ (end - start);
    for (let i = start; i < end; i++) hash = Math.imul(hash ^ data[i], 0x01000193);
    hash >>>= 0;
    const mask = this.table.length - 1;
    let slot = hash & mask;
    for (let entry = this.table[slot]; entry >= 0; entry = this.table[slot]) {
      if (
        this.hash[entry] === hash &&
        this.font[entry] === fontId &&
        this.same(entry, data, start, end)
      )
        return entry;
      slot = (slot + 1) & mask;
    }
    return this.measure(text, font, fontId, cps, start, end, hash, slot);
  }

  private same(entry: number, data: Uint32Array, start: number, end: number): boolean {
    const first = this.cpFirst[entry];
    if (this.cpEnd[entry] - first !== end - start) return false;
    for (let i = start; i < end; i++) if (this.cps[first + i - start] !== data[i]) return false;
    return true;
  }

  private measure(
    text: TextSource,
    font: FontHandle,
    fontId: number,
    cps: CodePoints,
    start: number,
    end: number,
    hash: number,
    slot: number,
  ): number {
    const scratch = this.scratch;
    scratch.clear();
    scratch.measureRun(text, font, cps, start, end);
    const entry = this.runs;
    this.reserve(scratch.count, end - start);
    this.runs++;
    this.hash[entry] = hash;
    this.font[entry] = fontId;
    this.cpFirst[entry] = this.cpLength;
    this.cps.set(cps.data.subarray(start, end), this.cpLength);
    this.cpLength += end - start;
    this.cpEnd[entry] = this.cpLength;
    const ink = this.ink;
    ink[entry * INK] = scratch.left;
    ink[entry * INK + 1] = scratch.top;
    ink[entry * INK + 2] = scratch.right;
    ink[entry * INK + 3] = scratch.bottom;
    ink[entry * INK + 4] = scratch.advance;
    const first = this.glyphCount;
    const pool = this.glyphs;
    for (let g = 0; g < scratch.count; g++) {
      const glyph = scratch.glyphs[g];
      const at = (first + g) * GLYPH;
      pool[at] = scratch.x[g];
      pool[at + 1] = scratch.y[g];
      pool[at + 2] = glyph.width;
      pool[at + 3] = glyph.height;
      pool[at + 4] = glyph.u;
      pool[at + 5] = glyph.v;
    }
    this.glyphCount = first + scratch.count;
    this.glyphFirst[entry] = first;
    this.glyphEnd[entry] = this.glyphCount;
    this.table[slot] = entry;
    if (this.runs * 2 > this.table.length) this.rehash();
    return entry;
  }

  private reserve(glyphs: number, codePoints: number): void {
    if (this.runs + 1 > this.hash.length) {
      const size = this.hash.length * 2;
      this.hash = grow(this.hash, size);
      this.font = grow(this.font, size);
      this.cpFirst = grow(this.cpFirst, size);
      this.cpEnd = grow(this.cpEnd, size);
      this.glyphFirst = grow(this.glyphFirst, size);
      this.glyphEnd = grow(this.glyphEnd, size);
      this.ink = grow(this.ink, size * INK);
    }
    if ((this.glyphCount + glyphs) * GLYPH > this.glyphs.length)
      this.glyphs = grow(
        this.glyphs,
        Math.max(this.glyphs.length * 2, (this.glyphCount + glyphs) * GLYPH),
      );
    if (this.cpLength + codePoints > this.cps.length)
      this.cps = grow(this.cps, Math.max(this.cps.length * 2, this.cpLength + codePoints));
  }

  private rehash(): void {
    const table = new Int32Array(this.table.length * 2).fill(-1);
    const mask = table.length - 1;
    for (let entry = 0; entry < this.runs; entry++) {
      let slot = this.hash[entry] & mask;
      while (table[slot] >= 0) slot = (slot + 1) & mask;
      table[slot] = entry;
    }
    this.table = table;
  }
}

function grow<T extends Uint32Array | Int32Array | Float64Array>(array: T, size: number): T {
  const grown = new (array.constructor as new (n: number) => T)(size);
  grown.set(array);
  return grown;
}

const caches = new WeakMap<TextSource, RunCache>();

/** The run cache of a text source, shared by every lane that lays out on it. */
export function runCacheOf(text: TextSource): RunCache {
  let cache = caches.get(text);
  if (!cache) {
    cache = new RunCache();
    caches.set(text, cache);
  }
  return cache;
}
