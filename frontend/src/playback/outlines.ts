import type { CellRect } from "../overlay/painter";
import { intersection } from "./viewport";

/**
 * An outline to draw over the picture of a lane, in CSS pixels of the canvas. `rect` is the object's own contour, never
 * reshaped; `clip` is the lane's cell, which the stroke is cut to (the whole canvas when absent), so it may reach into
 * the bars around the picture. `scale` is the shorter side of the lane's zoomed picture, which the stroke width is a
 * fraction of.
 */
export type Outline = {
  lane: number;
  rect: CellRect;
  clip?: CellRect;
  kind: "hover" | "selected";
  scale: number;
};

/** Stroke width as a fraction of the picture's shorter side; the selected object reads heavier than the hovered one. */
const STROKE = { hover: 0.004, selected: 0.009 };

/** The width in CSS pixels of an outline's stroke, which is centered on the contour. */
export function strokeWidth(kind: Outline["kind"], scale: number): number {
  return STROKE[kind] * scale;
}

/** Whether an outline of `rect` stroked `stroke` wide shows in `clip`: a box without area never does. */
export function reaches(rect: CellRect, clip: CellRect, stroke: number): boolean {
  if (rect.width <= 0 || rect.height <= 0) return false;
  const half = stroke / 2;
  return !!intersection(
    {
      x: rect.x - half,
      y: rect.y - half,
      width: rect.width + stroke,
      height: rect.height + stroke,
    },
    clip,
  );
}
