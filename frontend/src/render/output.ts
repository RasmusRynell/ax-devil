/** The size of an export's pictures and where its lanes go in them. */

import type { OutputWire, SizeWire } from "../generated/api";
import { columnsFor, layoutLanes } from "../playback/laneCells";
import type { LaneCell } from "../playback/renderer";

/** Several lanes tiled are scaled down to fit this, never up. A single lane keeps its native size. */
const TILED_LIMIT: SizeWire = { width: 3840, height: 2160 };

/** The canvas an export draws on, and the lane cells on it. */
export type Output = {
  /** Pixels of each output picture; both even for I420 (video), exact for Rgba (a still). */
  width: number;
  height: number;
  /** The grid of `lanes` cells, as the viewer tiles them. A cell may reach one pixel past an I420 canvas cropped to even. */
  cells: LaneCell[];
};

/**
 * The output read back as `format` for `lanes` lanes of pictures of `picture` pixels: the pictures side by side in
 * the viewer's grid at their native size, scaled down to `TILED_LIMIT` when there are several lanes. I420 is cropped
 * to even sizes; an Rgba still keeps its exact size.
 */
export function outputOf(picture: SizeWire, lanes: number, format: OutputWire): Output {
  const columns = columnsFor(lanes);
  const rows = Math.ceil(lanes / columns);
  const whole = { width: columns * picture.width, height: rows * picture.height };
  const scale =
    lanes === 1
      ? 1
      : Math.min(1, TILED_LIMIT.width / whole.width, TILED_LIMIT.height / whole.height);
  const width = Math.max(1, Math.round(whole.width * scale));
  const height = Math.max(1, Math.round(whole.height * scale));
  const crop = format === "I420" ? 2 : 1;
  return {
    width: width - (width % crop),
    height: height - (height % crop),
    cells: layoutLanes(
      Array.from({ length: lanes }, (_, lane) => lane),
      width,
      height,
    ),
  };
}
