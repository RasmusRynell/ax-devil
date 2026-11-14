/** How wide a table's text is, measured on a canvas so that no layout is read. */

import { KINDS, type Column } from "./columns";
import type { Measure } from "./fit";

/** A face to measure with: its CSS `font` and the width of one character. */
export interface Font {
  css: string;
  ch: number;
}

/** The faces of a table and the padding a cell spends beside its text. */
export interface Fonts {
  body: Font;
  /** The face of a monospace cell. */
  mono: Font;
  small: Font;
  pad: number;
}

/** Characters a header spends on the sort arrow beside its title. */
const ARROW_CHARS = 3;
/** Characters a categorized cell spends on its dot and the gap after it. */
const DOT_CHARS = 2;
/** The letter spacing of a header's capitals, in characters (0.08em). */
const TRACKING_CHARS = 0.15;
/** Most distinct texts kept per face before the cache starts over. */
const CACHE_LIMIT = 4000;

const cache = new Map<string, Map<string, number>>();
let context: OffscreenCanvasRenderingContext2D | null | undefined;

function canvas(): OffscreenCanvasRenderingContext2D | null {
  if (context === undefined)
    context =
      typeof OffscreenCanvas === "undefined" ? null : new OffscreenCanvas(1, 1).getContext("2d");
  return context;
}

/** The width of `text` in `font`; where there is no canvas, as many characters as it has. */
export function measureText(font: Font, text: string): number {
  const ctx = canvas();
  if (!ctx) return text.length * font.ch;
  let known = cache.get(font.css);
  if (!known) cache.set(font.css, (known = new Map()));
  let width = known.get(text);
  if (width === undefined) {
    if (known.size >= CACHE_LIMIT) known.clear();
    ctx.font = font.css;
    width = ctx.measureText(text).width;
    known.set(text, width);
  }
  return width;
}

const px = (value: string, fallback: number): number => {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

/** The face `element` is drawn in. */
export function fontOf(element: Element): Font {
  const style = getComputedStyle(element);
  const size = px(style.fontSize, 14);
  const css = `${style.fontStyle} ${style.fontWeight} ${size}px ${style.fontFamily}`;
  const font = { css, ch: size / 2 };
  return { css, ch: measureText(font, "0") };
}

/** The faces of a table's rows (read from `scroller`) and header (from `header`), and a cell's right padding. */
export function fontsOf(scroller: Element, header: Element): Fonts {
  const cell = header.firstElementChild;
  // A cell of a monospace column, as the table draws it, is the one thing to measure its face by.
  const probe = document.createElement("span");
  probe.className = "font-mono text-aside";
  scroller.append(probe);
  const mono = fontOf(probe);
  probe.remove();
  return {
    body: fontOf(scroller),
    mono,
    small: fontOf(header),
    pad: cell ? px(getComputedStyle(cell).paddingRight, 0) : 0,
  };
}

/** Every digit of a numeric column is as wide as the widest, so its texts are measured with zeros. */
const tabular = (text: string): string => text.replace(/\d/g, "0");

/** What each of `cols` needs for the `rows` in view; a row that is not loaded is skipped. */
export function measureColumns<Row>(
  cols: readonly Column<Row>[],
  rows: readonly (Row | undefined)[],
  { body, mono, small, pad }: Fonts,
): Measure[] {
  return cols.map((column) => {
    const kind = KINDS[column.kind];
    const face = kind.mono ? mono : body;
    const title =
      measureText(small, column.title.toUpperCase()) +
      (column.title.length * TRACKING_CHARS + (column.sortable ? ARROW_CHARS : 0)) * small.ch +
      1;
    const leastChars = column.least ?? kind.least;
    const least = Math.ceil(leastChars * face.ch) + 1;
    let comfortable = Math.ceil(Math.max(kind.comfortable, leastChars) * face.ch) + 1;
    let longest = comfortable;
    let empty = false;
    // The dot before a categorized cell's text.
    const dot = column.category ? DOT_CHARS * face.ch : 0;
    const read = column.text;
    if (read) {
      const texts = rows.flatMap((row) => (row === undefined ? [] : [read(row)]));
      longest = Math.max(
        0,
        ...texts.map((text) => measureText(face, kind.numeric ? tabular(text) : text) + 1),
      );
      if ((column.priority ?? 0) > 0 && !column.stretch) comfortable = longest;
      empty = texts.every((text) => text === "");
    }
    const floor = Math.max(Math.min(comfortable, longest), title) + pad + dot;
    return {
      natural: Math.max(longest, title) + pad + dot,
      floor,
      least: Math.min(Math.max(least, title) + pad + dot, floor),
      empty,
    };
  });
}
