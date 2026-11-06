import { useEffect, useRef, useState, type DragEvent, type ReactNode, type RefObject } from "react";
import { IconButton } from "../kit/IconButton";
import { PaneActions } from "./actions";
import { dropSide, type Rect, type Side } from "./layout";

/** What a header drag carries: the id of the pane. Nothing else is accepted by a pane. */
export const PANE_MIME = "application/x-ax-devil-pane";

const PIN = "Pin: keep this pane open when other content is opened";
const UNPIN = "Unpin: let opened content replace this pane";
const STATE = {
  pinned: "Pinned: opened content will not replace this pane",
  preview: "Preview (italic): opened content replaces this pane while it is active",
};

/**
 * The half a hint fills, by the side a drop would take. [AI] The fill is the accent at 30% (the reference's alpha 72
 * of 255 is 28%) inside an opaque accent edge, which reads as the darker border the reference draws.
 */
const HALF: Record<Side, string> = {
  left: "inset-y-0 left-0 w-1/2",
  right: "inset-y-0 right-0 w-1/2",
  top: "inset-x-0 top-0 h-1/2",
  bottom: "inset-x-0 bottom-0 h-1/2",
};

/**
 * One pane of the split area: a header with the title, the pin and the close button, over the viewer in `children`.
 * It sits at `rect` in the area. A press or the focus anywhere inside makes it the active pane. The header drags the
 * pane (`dragged` holds the id of the pane being dragged, which only this window's panes accept), and the pane
 * takes drops of another pane: while one is over it, the half it would land on is filled, and a drop calls `onDrop`
 * with the pane dropped and the side. Only the active pane offers its viewer's commands to the shell.
 */
export function Pane({
  viewer,
  title,
  pinned,
  active,
  rect,
  canPin,
  canClose,
  dragged,
  onActivate,
  onPin,
  onClose,
  onDrop,
  children,
}: {
  viewer: number;
  title: string;
  pinned: boolean;
  active: boolean;
  rect: Rect;
  canPin: boolean;
  canClose: boolean;
  dragged: RefObject<number | null>;
  onActivate: () => void;
  onPin: (pin: boolean) => void;
  onClose: () => void;
  onDrop: (source: number, side: Side) => void;
  children: ReactNode;
}) {
  const [hint, setHint] = useState<Side | null>(null);
  // Enters minus leaves: a drag moving between this pane's children leaves and enters them without leaving the pane.
  const over = useRef(0);
  const onButton = useRef(false);
  const clear = () => {
    over.current = 0;
    setHint(null);
  };
  // However the drag ends (a drop elsewhere, Escape), the hint goes.
  useEffect(() => {
    if (hint === null) return;
    window.addEventListener("dragend", clear);
    return () => window.removeEventListener("dragend", clear);
  }, [hint]);

  const sideOf = (event: DragEvent<HTMLElement>): Side => {
    const box = event.currentTarget.getBoundingClientRect();
    return dropSide(event.clientX - box.left, event.clientY - box.top, box.width, box.height);
  };
  const takes = (event: DragEvent<HTMLElement>) =>
    dragged.current !== null &&
    dragged.current !== viewer &&
    event.dataTransfer.types.includes(PANE_MIME);

  return (
    <div
      role="group"
      aria-label={title}
      aria-current={active || undefined}
      data-cursor-id={`pane:${viewer}`}
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
      className="absolute flex min-h-0 min-w-0 flex-col overflow-hidden bg-surface"
      onPointerDownCapture={() => !active && onActivate()}
      onFocusCapture={() => !active && onActivate()}
      onDragEnter={(event) => {
        if (!takes(event)) return;
        over.current += 1;
        event.preventDefault();
      }}
      onDragOver={(event) => {
        if (!takes(event)) return clear();
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        const side = sideOf(event);
        setHint((now) => (now === side ? now : side));
      }}
      onDragLeave={() => {
        over.current -= 1;
        if (over.current <= 0) clear();
      }}
      onDrop={(event) => {
        const source = Number(event.dataTransfer.getData(PANE_MIME));
        const side = sideOf(event);
        clear();
        if (!takes(event) || !Number.isInteger(source) || source === viewer) return;
        event.preventDefault();
        onDrop(source, side);
      }}
    >
      <div
        draggable
        title={`${title}\n${pinned ? STATE.pinned : STATE.preview}`}
        onPointerDown={(event) => {
          onButton.current = (event.target as Element).closest("button") !== null;
        }}
        onDragStart={(event) => {
          // A press on a header button is a click, not the start of a drag.
          if (onButton.current) return event.preventDefault();
          event.dataTransfer.setData(PANE_MIME, String(viewer));
          event.dataTransfer.effectAllowed = "move";
          dragged.current = viewer;
        }}
        onDragEnd={() => {
          dragged.current = null;
        }}
        className={`flex h-row shrink-0 items-center border-b-edge bg-panel ${
          active ? "border-accent" : "border-line"
        }`}
      >
        <span className={`min-w-0 flex-1 truncate px-m ${pinned ? "font-semibold" : "italic"}`}>
          {title}
        </span>
        <IconButton
          icon={pinned ? "pin-filled" : "pin"}
          label={pinned ? UNPIN : PIN}
          aria-pressed={pinned}
          data-cursor-id={`pane-pin:${viewer}`}
          disabled={!canPin}
          onClick={() => onPin(!pinned)}
        />
        <IconButton
          icon="x"
          label="Close this pane"
          data-cursor-id={`pane-close:${viewer}`}
          disabled={!canClose}
          onClick={onClose}
        />
      </div>
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <PaneActions active={active}>{children}</PaneActions>
      </div>
      {hint && (
        <div
          aria-hidden
          className={`pointer-events-none absolute z-10 border border-accent bg-accent/30 ${HALF[hint]}`}
        />
      )}
    </div>
  );
}
