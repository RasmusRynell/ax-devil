/** What the video keeps of the viewer's width while the media tools are open, in pixels. */
export const VIDEO_LEAST = 160;

/** The widths a media-tools panel may take, in pixels. */
export interface PanelRoom {
  /** What the content needs; the panel is never narrower, and a drag below half of it closes the panel. */
  least: number;
  /** What the video's room allows, but never less than `least`: the content comes before the video. */
  most: number;
}

/** The room of a panel `width` wide beside a video `video` wide, whose content needs `least`. */
export function roomOf(width: number, video: number, least: number): PanelRoom {
  return { least, most: Math.max(least, width + video - VIDEO_LEAST) };
}

/** `request` pixels for the panel after a drag or key press: the width it takes, and whether it asks to close. */
export function settle(
  request: number,
  { least, most }: PanelRoom,
): { width: number; close: boolean } {
  return { width: Math.min(most, Math.max(least, request)), close: request < least / 2 };
}
