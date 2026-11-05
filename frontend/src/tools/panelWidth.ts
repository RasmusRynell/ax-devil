import type { CSSProperties } from "react";

/** What the video keeps of the viewer's width while the media tools are open, in pixels. */
export const VIDEO_LEAST = 160;

/**
 * The style of a panel docked beside a video in a size container (the viewer), and the classes that read it. The
 * width is what the user chose, else 48ch up to half the container; it stays between `--panel-least` (what the
 * content needs, never more than the container) and what leaves the video `VIDEO_LEAST`. Where even the least leaves
 * the video less than that, the least wins (`min-width` beats `max-width`) and the video takes what is left. This is
 * the rule `roomOf` and `settle` apply to a drag.
 */
export function panelStyle(width: number | null): CSSProperties {
  return {
    "--panel-least": "min(40ch, 100cqw)",
    "--video-least": `${VIDEO_LEAST}px`,
    ...(width === null ? {} : { "--panel-width": `${width}px` }),
  } as CSSProperties;
}

/** The classes of a docked panel: width, its limits and the divider's anchor. */
export const PANEL_CLASS =
  "relative z-10 flex shrink-0 w-[var(--panel-width,min(var(--spacing-panel),50cqw))] min-w-(--panel-least) max-w-[calc(100cqw-var(--video-least))] flex-col border-l border-line bg-panel text-fg";

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
