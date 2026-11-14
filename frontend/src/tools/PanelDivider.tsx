import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { halfOf, roomOf, settle, type PanelRoom } from "./panelWidth";

/** Keys step the panel by this share of its least width. */
const STEP_SHARE = 8;

type Measured = PanelRoom & { now: number };

/** Which side of the window a panel is docked to; its divider is on the other edge. */
export type Dock = "left" | "right";

/**
 * The panel's width now and its room, read off the layout. Docked right, the video is the panel's previous sibling;
 * docked left, the activity strip is, and the panel takes at most half of the space beside it.
 */
function measure(panel: HTMLElement | null, dock: Dock): Measured {
  if (!panel) return { now: 0, least: 0, most: 0 };
  const now = panel.getBoundingClientRect().width;
  const least = parseFloat(getComputedStyle(panel).minWidth) || 0;
  const before = panel.previousElementSibling;
  const room =
    dock === "right"
      ? roomOf(now, before?.clientWidth ?? 0, least)
      : halfOf((panel.parentElement?.clientWidth ?? 0) - (before?.clientWidth ?? 0), least);
  return { now: Math.round(now), least: Math.round(room.least), most: Math.round(room.most) };
}

/**
 * The divider on the inner edge of the panel that holds it (the media tools, docked right; the sidebar, `left`), a
 * separator that drags and takes arrow keys. `onResize` gets every width on the way and `final` once it is chosen; a
 * drag that ends below half the panel's least width calls `onClose` instead. Home and End go to the least and the
 * most. The divider sits inside the panel's edge; `className` adds to its classes.
 */
export function PanelDivider({
  label,
  dock = "right",
  onResize,
  onClose,
  className = "",
}: {
  label: string;
  dock?: Dock;
  onResize: (width: number, final: boolean) => void;
  onClose: () => void;
  className?: string;
}) {
  // Moving toward the window's centre widens the panel.
  const toward = dock === "right" ? -1 : 1;
  const [room, setRoom] = useState<Measured>({ now: 0, least: 0, most: 0 });
  const drag = useRef<{ x: number; width: number; room: PanelRoom; request: number } | null>(null);
  const self = useRef<HTMLDivElement>(null);
  const read = useCallback(() => {
    const next = measure(self.current?.parentElement ?? null, dock);
    setRoom((was) =>
      was.now === next.now && was.least === next.least && was.most === next.most ? was : next,
    );
    return next;
  }, [dock]);

  // After every render, and when the panel or the room around it changes size.
  useLayoutEffect(() => {
    read();
  });
  useLayoutEffect(() => {
    const element = self.current?.parentElement;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(element);
    if (element.parentElement) observer.observe(element.parentElement);
    return () => observer.disconnect();
  }, [read]);

  const end = (event: PointerEvent<HTMLElement>) => {
    const started = drag.current;
    drag.current = null;
    // A click that moved nothing keeps the width; a lost capture after a pointer-up finds nothing to end.
    if (!started || started.request === started.width) return;
    const { width, close } = settle(started.request, started.room);
    if (close && event.type === "pointerup") onClose();
    else onResize(width, true);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const next = read();
    const step = Math.max(1, Math.round(next.least / STEP_SHARE));
    const request = new Map([
      ["ArrowLeft", next.now - toward * step],
      ["ArrowRight", next.now + toward * step],
      ["Home", next.least],
      ["End", next.most],
    ]).get(event.key);
    if (request === undefined) return;
    event.preventDefault();
    onResize(settle(request, next).width, true);
  };

  return (
    <div
      ref={self}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={room.now}
      aria-valuemin={room.least}
      aria-valuemax={room.most}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        const start = read();
        drag.current = { x: event.clientX, width: start.now, room: start, request: start.now };
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (!start || event.buttons === 0) return;
        start.request = start.width + toward * (event.clientX - start.x);
        onResize(settle(start.request, start.room).width, false);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={end}
      className={`absolute inset-y-0 ${dock === "right" ? "left-0" : "right-0"} z-10 w-s cursor-col-resize hover:bg-line-strong focus-ring ${className}`}
    />
  );
}
