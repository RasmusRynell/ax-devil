import { CATEGORY_COLORS } from "../playback/laneLayouts";
import { crc32 } from "../present";

const encoder = new TextEncoder();

/** The dot's color by category index, written out so the stylesheet generates them. */
const DOTS = [
  "bg-category-0",
  "bg-category-1",
  "bg-category-2",
  "bg-category-3",
  "bg-category-4",
  "bg-category-5",
  "bg-category-6",
  "bg-category-7",
];

/** The background class of the dot for class `name`: the color the hover chip gives it, whatever its case. */
export function categoryDot(name: string): string {
  return DOTS[crc32(encoder.encode(name.toLowerCase())) % CATEGORY_COLORS];
}
