import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { shareAt, type Path, type SeamInfo } from "./layout";

/** [AI] An arrow key moves the seam this far, in CSS pixels: a pane's least width divided by ten. */
const STEP = 24;

/**
 * The seam between the two halves of a split: a separator that drags and takes arrow keys, centred on the boundary
 * and invisible until the pointer or the keyboard is on it. `onResize` gets the share the first half would have
 * (in 10000ths) on every step of a drag with `final` false, and once with `final` true where the drag ends or a key
 * is pressed. The seam never moves a half below its least size. Home and End go to the least and the most.
 */
export function Seam({
  seam,
  onResize,
}: {
  seam: SeamInfo;
  onResize: (path: Path, share: number, final: boolean) => void;
}) {
  const { axis, rect, offset, least, most, path } = seam;
  const row = axis === "row";
  const drag = useRef<{ from: number; offset: number; last: number; moved: boolean } | null>(null);
  const within = (at: number) => Math.max(least, Math.min(most, at));
  const along = (event: PointerEvent<HTMLElement>) => (row ? event.clientX : event.clientY);

  const end = () => {
    const started = drag.current;
    drag.current = null;
    // A click that moved nothing has shown nothing to end; a lost capture after a pointer-up finds nothing either.
    // A drag that came back to where it began still ends, so the area stops showing its steps.
    if (started?.moved) onResize(path, shareAt(seam, started.last), true);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const [back, forward] = row ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
    const request = new Map([
      [back, offset - STEP],
      [forward, offset + STEP],
      ["Home", least],
      ["End", most],
    ]).get(event.key);
    if (request === undefined) return;
    // The arrow keys are the seam's own here, not the video's ten-frame steps the shell would take them for.
    event.preventDefault();
    event.stopPropagation();
    onResize(path, shareAt(seam, within(request)), true);
  };

  return (
    <div
      role="separator"
      aria-orientation={row ? "vertical" : "horizontal"}
      aria-label="Resize panes"
      aria-valuenow={Math.round(offset)}
      aria-valuemin={Math.round(least)}
      aria-valuemax={Math.round(most)}
      tabIndex={0}
      data-cursor-id={`seam:${path.join("") || "root"}`}
      onKeyDown={onKeyDown}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { from: along(event), offset, last: offset, moved: false };
      }}
      onPointerMove={(event) => {
        const started = drag.current;
        if (!started || event.buttons === 0) return;
        started.moved = true;
        started.last = within(started.offset + along(event) - started.from);
        onResize(path, shareAt(seam, started.last), false);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={end}
      style={
        row
          ? { left: rect.x + offset, top: rect.y, height: rect.h }
          : { left: rect.x, top: rect.y + offset, width: rect.w }
      }
      className={`absolute z-10 hover:bg-line-strong focus-ring ${
        row ? "w-s -translate-x-1/2 cursor-col-resize" : "h-s -translate-y-1/2 cursor-row-resize"
      }`}
    />
  );
}
