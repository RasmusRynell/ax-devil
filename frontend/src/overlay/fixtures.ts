/**
 * Builds Overlay sections from plain rows, laid out as the backend's encoder lays them out in an envelope (8-byte
 * aligned, after a section header). For tests of the decoder, the preparation and the session.
 */

import {
  OVERLAY_TAG_BOXES,
  OVERLAY_TAG_CIRCLES,
  OVERLAY_TAG_LABEL_STYLES,
  OVERLAY_TAG_LABELS,
  OVERLAY_TAG_LINES,
  OVERLAY_TAG_PAINTS,
  OVERLAY_TAG_PATH_POINTS,
  OVERLAY_TAG_POINTS,
  OVERLAY_TAG_POLYGONS,
  OVERLAY_TAG_POLYLINES,
  OVERLAY_TAG_RESERVED,
  OVERLAY_TAG_RUNS,
  OVERLAY_TAG_STRINGS,
  OVERLAY_TAG_TEXTS,
} from "../generated/api";
import type { GlyphSource } from "./atlas";
import { NO_STRING } from "./instructions";

type Rgb = [number, number, number];
type Rgba = [number, number, number, number];

/** A paint; lengths relative to min(target width, height). */
export type PaintRow = {
  pen?: Rgb;
  dashed?: boolean;
  penWidth?: number;
  brush?: Rgba;
  fontFamily?: string;
  fontSize?: number;
  radius?: number;
};
/** A label style; lengths relative to min(target width, height). */
export type LabelStyleRow = {
  size: number;
  family?: string;
  background?: Rgba;
  paddingX?: number;
  paddingY?: number;
  radius?: number;
  gap?: number;
};
/** A label run: a text, or a bar when `bar` is set. */
export type RunRow = { text?: string; bar?: number; rgb?: Rgb; weight?: number };
type PathRow = { points: [number, number][]; paint: number };

/** One drawing's rows, normalized coordinates, tables referenced by index. */
export type DrawingRows = {
  paints?: PaintRow[];
  boxes?: [number, number, number, number, number][];
  lines?: [number, number, number, number, number][];
  circles?: [number, number, number, number][];
  points?: [number, number, number][];
  polygons?: PathRow[];
  polylines?: PathRow[];
  texts?: { x: number; y: number; text: string; anchor?: number; paint: number }[];
  labelStyles?: LabelStyleRow[];
  labels?: { x: number; y: number; anchor?: number; style: number; runs: RunRow[] }[];
};

/** Writes 4-byte words of one section. */
class Words {
  readonly view: DataView;
  at = 0;
  constructor(bytes: number) {
    this.view = new DataView(new ArrayBuffer(bytes));
  }
  f(value: number): this {
    this.view.setFloat32(this.at, value, true);
    this.at += 4;
    return this;
  }
  u(value: number): this {
    this.view.setUint32(this.at, value >>> 0, true);
    this.at += 4;
    return this;
  }
  bytes(values: number[]): this {
    values.forEach((value, index) => this.view.setUint8(this.at + index, value));
    this.at += 4;
    return this;
  }
}

/** The sections of `rows` by tag, each a view into one buffer, plus an unknown reserved section the decoder skips. */
export function encodeSections(rows: DrawingRows): Map<number, DataView> {
  const strings: string[] = [];
  const id = (text: string | undefined) => {
    if (text === undefined) return NO_STRING;
    const found = strings.indexOf(text);
    return found >= 0 ? found : strings.push(text) - 1;
  };
  const sections = new Map<number, Words>();
  const section = <T>(
    tag: number,
    items: T[] | undefined,
    words: number,
    write: (out: Words, item: T) => void,
  ) => {
    if (!items?.length) return;
    const out = new Words(items.length * words * 4);
    items.forEach((item) => write(out, item));
    sections.set(tag, out);
  };
  section(OVERLAY_TAG_PAINTS, rows.paints, 6, (out, p) =>
    out
      .bytes([...(p.pen ?? [0, 0, 0]), p.dashed ? 1 : 0])
      .f(p.penWidth ?? 0)
      .bytes(p.brush ?? [0, 0, 0, 0])
      .u(id(p.fontFamily))
      .f(p.fontSize ?? 0)
      .f(p.radius ?? 0),
  );
  section(OVERLAY_TAG_BOXES, rows.boxes, 5, (out, [x, y, w, h, paint]) =>
    out.f(x).f(y).f(w).f(h).u(paint),
  );
  section(OVERLAY_TAG_LINES, rows.lines, 5, (out, [a, b, c, d, paint]) =>
    out.f(a).f(b).f(c).f(d).u(paint),
  );
  section(OVERLAY_TAG_CIRCLES, rows.circles, 4, (out, [x, y, r, paint]) =>
    out.f(x).f(y).f(r).u(paint),
  );
  section(OVERLAY_TAG_POINTS, rows.points, 3, (out, [x, y, paint]) => out.f(x).f(y).u(paint));
  const points: [number, number][] = [];
  const paths = (list: PathRow[] | undefined) =>
    list?.map(({ points: corners, paint }) => {
      const first = points.length;
      points.push(...corners);
      return [first, corners.length, paint];
    });
  const write = (out: Words, [first, count, paint]: number[]) => out.u(first).u(count).u(paint);
  section(OVERLAY_TAG_POLYGONS, paths(rows.polygons), 3, write);
  section(OVERLAY_TAG_POLYLINES, paths(rows.polylines), 3, write);
  section(OVERLAY_TAG_PATH_POINTS, points, 2, (out, [x, y]) => out.f(x).f(y));
  section(OVERLAY_TAG_TEXTS, rows.texts, 5, (out, t) =>
    out
      .f(t.x)
      .f(t.y)
      .u(id(t.text))
      .bytes([t.anchor ?? 0, 0, 0, 0])
      .u(t.paint),
  );
  section(OVERLAY_TAG_LABEL_STYLES, rows.labelStyles, 7, (out, s) =>
    out
      .f(s.size)
      .u(id(s.family))
      .bytes(s.background ?? [0, 0, 0, 0])
      .f(s.paddingX ?? 0)
      .f(s.paddingY ?? 0)
      .f(s.radius ?? 0)
      .f(s.gap ?? 0),
  );
  const runs: RunRow[] = [];
  section(OVERLAY_TAG_LABELS, rows.labels, 6, (out, l) => {
    out
      .f(l.x)
      .f(l.y)
      .bytes([l.anchor ?? 0, 0, 0, 0])
      .u(l.style)
      .u(runs.length)
      .u(l.runs.length);
    runs.push(...l.runs);
  });
  section(OVERLAY_TAG_RUNS, runs, 3, (out, r) =>
    out
      .u(r.bar === undefined ? id(r.text ?? "") : NO_STRING)
      .f(r.bar ?? Number.NaN)
      .bytes([...(r.rgb ?? [255, 255, 255]), r.weight ?? 0]),
  );
  const utf8 = strings.map((text) => new TextEncoder().encode(text));
  const blob = utf8.reduce((sum, bytes) => sum + bytes.length, 0);
  const table = new Words(4 + (strings.length + 1) * 4 + blob);
  table.u(strings.length);
  let offset = 0;
  table.u(0);
  for (const bytes of utf8) table.u((offset += bytes.length));
  for (const bytes of utf8) {
    new Uint8Array(table.view.buffer, table.at, bytes.length).set(bytes);
    table.at += bytes.length;
  }
  if (strings.length) sections.set(OVERLAY_TAG_STRINGS, table);
  sections.set(OVERLAY_TAG_RESERVED, new Words(12).u(1).u(2).u(3));
  // Lay every section out in one message, each after an 8-byte header and padded to 8, as the envelope does.
  const total = [...sections.values()].reduce(
    (sum, words) => sum + 8 + Math.ceil(words.view.byteLength / 8) * 8,
    64,
  );
  const message = new Uint8Array(total);
  const out = new Map<number, DataView>();
  let at = 64;
  for (const [tag, words] of sections) {
    at += 8;
    message.set(new Uint8Array(words.view.buffer), at);
    out.set(tag, new DataView(message.buffer, at, words.view.byteLength));
    at += Math.ceil(words.view.byteLength / 8) * 8;
  }
  return out;
}

/**
 * A glyph source with fixed metrics, for tests: every character advances 0.6 of the font size and inks from 0.7
 * above to 0.2 below the baseline, spaces ink nothing; ascent 0.8, descent 0.25, cap height 0.7, line 1.05.
 */
export function fakeGlyphs(): GlyphSource & { drawn: string[] } {
  const size = (font: string) => Number(/([\d.]+)px/.exec(font)?.[1] ?? 0);
  const drawn: string[] = [];
  return {
    drawn,
    metrics: (font) => {
      const px = size(font);
      return { ascent: 0.8 * px, descent: 0.25 * px, capHeight: 0.7 * px, lineHeight: 1.05 * px };
    },
    measure: (font, text) => {
      const px = size(font);
      const advance = text.length * 0.6 * px;
      if (text.trim() === "") return { advance, left: 0, top: 0, right: 0, bottom: 0 };
      return { advance, left: 0, top: -0.7 * px, right: advance, bottom: 0.2 * px };
    },
    draw: (_font, text) => void drawn.push(text),
    read: (_x, _y, width, height) => new Uint8Array(width * height).fill(255),
    clear: () => {
      drawn.length = 0;
    },
  };
}
