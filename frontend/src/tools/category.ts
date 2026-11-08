import { categoryIndex } from "../playback/laneLayouts";

/** The dot's color by category index, written out so the stylesheet generates them. */
const DOTS = [
  "bg-dot-0",
  "bg-dot-1",
  "bg-dot-2",
  "bg-dot-3",
  "bg-dot-4",
  "bg-dot-5",
  "bg-dot-6",
  "bg-dot-7",
];

/** The background class of the dot for class `name` on the panel: the hover chip's hue, deepened on a light theme. */
export function categoryDot(name: string): string {
  return DOTS[categoryIndex(name)];
}
