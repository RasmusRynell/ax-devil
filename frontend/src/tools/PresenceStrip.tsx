import type { MouseEvent } from "react";
import { categoryDot } from "./category";

/**
 * Where an object is across a video of `frames` frames: a groove with a mark for each inclusive `[first, last]` span,
 * in the color of `category`, a tick for each frame in `ticks` and a marker at the frame `current` on screen. With
 * `onSeek` a click asks for the frame under the pointer, clamped to the video; without it the strip only shows.
 */
export function PresenceStrip({
  spans,
  frames,
  category,
  ticks = [],
  current,
  onSeek,
}: {
  spans: readonly (readonly [number, number])[];
  frames: number;
  category: string;
  ticks?: readonly number[];
  current?: number;
  onSeek?: (frame: number) => void;
}) {
  const count = Math.max(frames, 1);
  const at = (frame: number) => (Math.min(Math.max(frame, 0), count) / count) * 100;
  const props = onSeek
    ? {
        role: "img",
        "aria-label": "Presence",
        onClick: (event: MouseEvent<HTMLElement>) => {
          const box = event.currentTarget.getBoundingClientRect();
          if (box.width <= 0) return;
          const frame = Math.floor(((event.clientX - box.left) / box.width) * count);
          onSeek(Math.min(count - 1, Math.max(0, frame)));
        },
      }
    : { "aria-hidden": true };
  return (
    <span
      {...props}
      className={`relative block h-icon overflow-hidden ${onSeek ? "cursor-pointer" : ""}`}
    >
      <span className="absolute inset-x-0 inset-y-s rounded-control bg-hover" />
      {spans.map(([first, last]) => (
        <span
          key={first}
          className={`absolute inset-y-s min-w-hairline ${categoryDot(category)}`}
          style={{ left: `${at(first)}%`, width: `${at(last + 1) - at(first)}%` }}
        />
      ))}
      {ticks.map((frame) => (
        <span
          key={frame}
          data-tick={frame}
          className="absolute inset-y-0 w-hairline bg-muted"
          style={{ left: `min(${at(frame)}%, calc(100% - var(--spacing-hairline)))` }}
        />
      ))}
      {current !== undefined && (
        <span
          className="absolute inset-y-0 w-xs bg-accent"
          style={{ left: `min(${at(current)}%, calc(100% - var(--spacing-xs)))` }}
        />
      )}
    </span>
  );
}
