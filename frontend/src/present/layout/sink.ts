import type { RunCache } from "../text/runCache";

/**
 * Where layout writes what it decided, in target CSS pixels. Colours are packed `0xRRGGBBAA`. A stroke is absent when
 * its width is 0 (the colour is then 0 too); a fill is absent when its alpha is 0. `QuadSink` turns the calls into
 * quads for the painter; the canonical sink of the tests records them as oracle rows.
 */
export interface PrimitiveSink {
  /** A pass starts (again, when the atlas filled up): discard what was written. */
  begin(): void;
  /** Every primitive of layer `index` has been written. */
  endLayer(index: number): void;
  box(
    left: number,
    top: number,
    width: number,
    height: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
    radius: number,
  ): void;
  circle(
    cx: number,
    cy: number,
    radius: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
  ): void;
  line(
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
  ): void;
  /** A closed path of `count` points from `first` in `points` (x, y pairs, fractions of the image) scaled by `scaleX`, `scaleY`. */
  polygon(
    points: Float32Array,
    first: number,
    count: number,
    scaleX: number,
    scaleY: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
  ): void;
  /**
   * A text mark's placement is chosen: the anchor point of its box is at (`x`, `y`) with anchor factors (`anchorX`,
   * `anchorY`: 0, 0.5 or 1 across and down). `record` is the mark's record in the `MarkBuffer`. The glyphs follow as
   * `glyph` calls.
   */
  text(record: number, x: number, y: number, anchorX: number, anchorY: number, size: number): void;
  /** A label's placement is chosen; its background, bars and glyphs follow as `rect` and `glyph` calls. */
  label(
    record: number,
    x: number,
    y: number,
    anchorX: number,
    anchorY: number,
    size: number,
    padX: number,
    padY: number,
    radius: number,
    gap: number,
  ): void;
  /** A filled rounded rectangle: a label's background or a bar part. */
  rect(
    left: number,
    top: number,
    width: number,
    height: number,
    radius: number,
    fill: number,
  ): void;
  /**
   * The glyphs of cached run `entry` (bitmaps and their top-left corners relative to a baseline origin, in device
   * pixels) with the origin at (`originX`, `originY`) device pixels, drawn in `color` at device ratio `dpr`. A run is
   * one call, so no number is boxed per glyph.
   */
  glyphs(
    runs: RunCache,
    entry: number,
    originX: number,
    originY: number,
    dpr: number,
    color: number,
  ): void;
}

/** A rectangle of the displayed image in CSS pixels. */
export interface Region {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The displayed image and how it is shown, in CSS pixels. */
export interface LayoutView {
  /** The displayed image, zoom and aspect applied. */
  width: number;
  height: number;
  /** Device pixels per CSS pixel, the UI zoom included. */
  dpr: number;
  /** The UI zoom; lengths in px are divided by it, so overlays keep their size when the interface is zoomed. */
  zoom: number;
  /**
   * The part of the displayed image on screen, when a zoom pushes the rest out of view: marks that lie entirely outside
   * it are not laid out. Absent or null: everything is.
   */
  visible?: Region | null;
}

/** A sink that takes nothing: culled marks run their checks against it so a row fails the same wherever it is. */
export const DISCARD: PrimitiveSink = {
  begin() {},
  endLayer() {},
  box() {},
  circle() {},
  line() {},
  polygon() {},
  text() {},
  label() {},
  rect() {},
  glyphs() {},
};
