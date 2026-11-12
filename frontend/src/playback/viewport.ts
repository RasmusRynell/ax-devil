/**
 * The zoom and pan of a viewer's pictures, as pure geometry. A viewport is size independent: `zoom` multiplies the
 * picture fitted into a cell, and the pan is the shift of the picture's centre from the cell's centre as a fraction
 * of the zoomed picture. Every lane cell of a viewer shows the same viewport; a cell whose aspect forces a tighter
 * clamp than the stored pan allows is clamped when it is drawn, without changing what is stored.
 */

import type { Viewport as Region } from "../generated/api";
import type { CellRect } from "../overlay/painter";
import type { Viewport } from "../lanes/contract";

export type Size = { width: number; height: number };

/** The whole picture fitted into the cell. */
export const FIT: Viewport = { zoom: 1, panX: 0, panY: 0 };
/** What one wheel notch or zoom command multiplies the zoom by. */
export const ZOOM_STEP = 1.15;
export const ZOOM_MAX = 10;

/** The picture of `size` fitted into `cell` keeping its aspect, centred, on whole pixels. */
export function fitPicture(cell: CellRect, size: Size): CellRect {
  const scale = Math.min(cell.width / size.width, cell.height / size.height);
  const width = Math.max(1, Math.floor(size.width * scale));
  const height = Math.max(1, Math.floor(size.height * scale));
  return {
    x: cell.x + Math.floor((cell.width - width) / 2),
    y: cell.y + Math.floor((cell.height - height) / 2),
    width,
    height,
  };
}

/** One axis of a zoomed picture: where the cell and the fitted picture lie, and the pan as a fraction of the zoomed length. */
type Axis = { cell: number; cellLength: number; base: number; baseLength: number };

/** The pan that keeps the zoomed picture over the whole cell; a picture that fits the cell stays centred. */
function clampPan(axis: Axis, zoom: number, pan: number): number {
  const length = axis.baseLength * zoom;
  if (length <= axis.cellLength) return 0;
  const centre = axis.base + axis.baseLength / 2;
  const least = (axis.cell + axis.cellLength - length / 2 - centre) / length;
  const most = (axis.cell + length / 2 - centre) / length;
  return Math.max(least, Math.min(most, pan));
}

function axes(cell: CellRect, image: Size): [Axis, Axis] {
  const base = fitPicture(cell, image);
  return [
    { cell: cell.x, cellLength: cell.width, base: base.x, baseLength: base.width },
    { cell: cell.y, cellLength: cell.height, base: base.y, baseLength: base.height },
  ];
}

/** `v` with its pan clamped for this cell. */
function settled(v: Viewport, cell: CellRect, image: Size): Viewport {
  const [x, y] = axes(cell, image);
  return { zoom: v.zoom, panX: clampPan(x, v.zoom, v.panX), panY: clampPan(y, v.zoom, v.panY) };
}

/** The picture in `cell` under `v`: fitted, zoomed, clamped to cover the cell, on whole pixels. It may reach past the cell. */
export function pictureRect(cell: CellRect, image: Size, v: Viewport): CellRect {
  if (v.zoom <= 1) return fitPicture(cell, image);
  const shown = settled(v, cell, image);
  const [x, y] = axes(cell, image);
  const edge = (axis: Axis, pan: number): [number, number] => {
    const length = axis.baseLength * v.zoom;
    const start = axis.base + axis.baseLength / 2 + pan * length - length / 2;
    return [Math.round(start), Math.round(start + length)];
  };
  const [left, right] = edge(x, shown.panX);
  const [top, bottom] = edge(y, shown.panY);
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** `v` zoomed by `factor` (bounded to 1 through `ZOOM_MAX`) keeping the image point under (`x`, `y`) of `cell` in place. */
export function zoomAt(
  v: Viewport,
  cell: CellRect,
  image: Size,
  x: number,
  y: number,
  factor: number,
): Viewport {
  const zoom = Math.max(1, Math.min(ZOOM_MAX, v.zoom * factor));
  if (zoom === v.zoom) return v;
  if (zoom <= 1) return FIT;
  const shown = settled(v, cell, image);
  const ratio = zoom / v.zoom;
  const [ax, ay] = axes(cell, image);
  const pan = (axis: Axis, anchor: number, at: number): number => {
    const centre = axis.base + axis.baseLength / 2;
    const shift = (anchor - centre) * (1 - ratio) + at * axis.baseLength * v.zoom * ratio;
    return clampPan(axis, zoom, shift / (axis.baseLength * zoom));
  };
  return { zoom, panX: pan(ax, x, shown.panX), panY: pan(ay, y, shown.panY) };
}

/** `v` moved by (`dx`, `dy`) pixels of `cell`; an unzoomed viewport does not move. */
export function panBy(v: Viewport, cell: CellRect, image: Size, dx: number, dy: number): Viewport {
  if (v.zoom <= 1) return v;
  const shown = settled(v, cell, image);
  const [x, y] = axes(cell, image);
  return {
    zoom: v.zoom,
    panX: clampPan(x, v.zoom, shown.panX + dx / (x.baseLength * v.zoom)),
    panY: clampPan(y, v.zoom, shown.panY + dy / (y.baseLength * v.zoom)),
  };
}

/** The point (`x`, `y`) as fractions of `picture` (0 to 1 across and down), or null when it is outside it. */
export function toImage(picture: CellRect, x: number, y: number): { x: number; y: number } | null {
  if (!(picture.width > 0 && picture.height > 0)) return null;
  const u = (x - picture.x) / picture.width;
  const v = (y - picture.y) / picture.height;
  return u >= 0 && u <= 1 && v >= 0 && v <= 1 ? { x: u, y: v } : null;
}

/** The part of `rect` inside `clip`, or null when they do not overlap. */
export function intersection(rect: CellRect, clip: CellRect): CellRect | null {
  const left = Math.max(rect.x, clip.x);
  const top = Math.max(rect.y, clip.y);
  const right = Math.min(rect.x + rect.width, clip.x + clip.width);
  const bottom = Math.min(rect.y + rect.height, clip.y + clip.height);
  return right > left && bottom > top
    ? { x: left, y: top, width: right - left, height: bottom - top }
    : null;
}

/**
 * The part of the video `v` shows in `cell`, as a rectangle of the frame: what a window shares so a window of another
 * shape can show the same region. Absent when the whole frame is shown.
 */
export function regionOf(v: Viewport, cell: CellRect, image: Size): Region | undefined {
  const picture = pictureRect(cell, image, v);
  const shown = intersection(picture, cell);
  if (!shown) return undefined;
  const w = shown.width / picture.width;
  const h = shown.height / picture.height;
  if (w >= 1 && h >= 1) return undefined;
  return {
    x: (shown.x - picture.x) / picture.width,
    y: (shown.y - picture.y) / picture.height,
    w,
    h,
  };
}

/**
 * The viewport that fits `region` inside `cell`, centred on it: the zoom is the least that fills the cell along one
 * axis, so a cell of another shape shows at least the region, bounded by `ZOOM_MAX`.
 */
export function viewportOf(region: Region | undefined, cell: CellRect, image: Size): Viewport {
  if (!region) return FIT;
  const base = fitPicture(cell, image);
  const zoom = Math.min(
    ZOOM_MAX,
    cell.width / (base.width * region.w),
    cell.height / (base.height * region.h),
  );
  if (zoom <= 1) return FIT;
  // The picture's centre sits at `pan` of its zoomed length from the cell's; the region's centre goes there.
  const pan = (
    cell: number,
    cellLength: number,
    base: number,
    baseLength: number,
    centre: number,
  ) => (cell + cellLength / 2 - (base + baseLength / 2)) / (baseLength * zoom) + 0.5 - centre;
  return settled(
    {
      zoom,
      panX: pan(cell.x, cell.width, base.x, base.width, region.x + region.w / 2),
      panY: pan(cell.y, cell.height, base.y, base.height, region.y + region.h / 2),
    },
    cell,
    image,
  );
}
