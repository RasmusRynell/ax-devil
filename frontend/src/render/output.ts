/** The size of an export's pictures and where its lanes go in them. */

import type { OutputWire, SizeWire } from "../generated/api";
import { columnsFor } from "../playback/laneCells";
import { NAME_TAG_SIZE } from "../playback/nameTags";
import type { Field, LaneCell } from "../playback/renderer";

/** Several lanes tiled are scaled down to fit this, never up. A single lane keeps its native size. */
const TILED_LIMIT: SizeWire = { width: 3840, height: 2160 };

/** A lane's name is set at a thirtieth of the cell's height, but never below the viewer's size. */
const NAME_PER_CELL = 30;

/** What an export's canvas is cleared to: black behind the cells and in the slots no lane fills. */
export const OUTPUT_FIELD: Field = { red: 0, green: 0, blue: 0 };

/** The canvas an export draws on, and the lane cells on it. */
export type Output = {
  /** Pixels of each output picture; both even for I420 (video), exact for Rgba (a still). */
  width: number;
  height: number;
  /** One cell per lane, all of one size, left to right and top to bottom. A cell may reach one pixel past an I420 canvas cropped to even. */
  cells: LaneCell[];
  /** The font size of the lane names, in pixels of the output. */
  nameSize: number;
};

/**
 * The output read back as `format` for `lanes` lanes of pictures of `picture` pixels: equal cells in the viewer's
 * column count at the pictures' native size, scaled down to `TILED_LIMIT` when there are several lanes. Slots left in
 * the last row stay empty. I420 is cropped to even sizes; an Rgba still keeps its exact size.
 */
export function outputOf(picture: SizeWire, lanes: number, format: OutputWire): Output {
  const columns = columnsFor(lanes);
  const rows = Math.ceil(lanes / columns);
  const scale =
    lanes === 1
      ? 1
      : Math.min(
          1,
          TILED_LIMIT.width / (columns * picture.width),
          TILED_LIMIT.height / (rows * picture.height),
        );
  const cell = {
    width: Math.max(1, Math.floor(picture.width * scale)),
    height: Math.max(1, Math.floor(picture.height * scale)),
  };
  const width = columns * cell.width;
  const height = rows * cell.height;
  const crop = format === "I420" ? 2 : 1;
  return {
    width: width - (width % crop),
    height: height - (height % crop),
    cells: Array.from({ length: lanes }, (_, lane) => ({
      lane,
      rect: {
        x: (lane % columns) * cell.width,
        y: Math.floor(lane / columns) * cell.height,
        ...cell,
      },
    })),
    nameSize: Math.max(NAME_TAG_SIZE, Math.floor(cell.height / NAME_PER_CELL)),
  };
}
