import type { CodePoints } from "../expr/codePoints";

/** Label and text weights, in the order of `WEIGHTS`. */
export const Weight = { Regular: 0, Medium: 1, Semibold: 2, Bold: 3 } as const;

/** Font-wide metrics in device pixels. */
export interface FontMetrics {
  readonly ascent: number;
  readonly descent: number;
  readonly capHeight: number;
  readonly lineHeight: number;
}

/** One bitmap on the atlas page: its advance, its offset from the baseline origin and its page rectangle, in device pixels. */
export interface Glyph {
  readonly advance: number;
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
  readonly u: number;
  readonly v: number;
}

/** A font at one device pixel size and weight. */
export interface FontHandle {
  readonly metrics: FontMetrics;
}

/** Thrown by `glyph` and `whole` when the page has no room; layout resets the source and lays out again. */
export class AtlasFull extends Error {
  constructor() {
    super("the text atlas page is full");
    this.name = "AtlasFull";
  }
}

/**
 * The glyph atlas as presentation sees it. The canvas-backed atlas in `overlay/atlas.ts` implements it; tests use a
 * fake with fixed metrics, so layout stays free of the DOM.
 */
export interface TextSource {
  /** Bumps whenever the page is reset, so cached layouts of the old page are known to be stale. */
  readonly generation: number;
  /** Leave out glyphs that do not fit instead of throwing `AtlasFull`; layout sets it while laying out on a fresh page. */
  dropOverflow: boolean;
  /** The font for a family (empty for the app's default), a size in device pixels and a `Weight`. */
  font(family: string, devicePixels: number, weight: number): FontHandle;
  /** The glyph of one code point; may throw `AtlasFull`. */
  glyph(font: FontHandle, codePoint: number): Glyph;
  /** A whole line as one bitmap, for scripts that cannot be drawn glyph by glyph; may throw `AtlasFull`. */
  whole(font: FontHandle, text: CodePoints, start: number, end: number): Glyph;
  /** Start a new page; every glyph handed out before is stale. */
  reset(): void;
}
