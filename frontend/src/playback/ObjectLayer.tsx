import { useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from "react";
import { placeChip } from "./chipPlacement";
import type { Picked, Selection } from "./contract";
import type { Geometry, Outline } from "./renderer";

/** How the canvas reports to the layer; the layer renders by itself, so the viewer does not render per frame. */
export type ObjectLayerHandle = {
  /** The object under the pointer changed (`null`: none). */
  hover(picked: Picked | null): void;
  /** The pictures were drawn: outlines follow the objects. */
  drawn(geometry: Geometry): void;
};

const NO_OUTLINES: readonly Outline[] = [];

const sameOutlines = (a: readonly Outline[], b: readonly Outline[]): boolean =>
  a.length === b.length &&
  a.every(
    (outline, i) =>
      outline.lane === b[i].lane &&
      outline.kind === b[i].kind &&
      outline.rect.x === b[i].rect.x &&
      outline.rect.y === b[i].rect.y &&
      outline.rect.width === b[i].rect.width &&
      outline.rect.height === b[i].rect.height,
  );

/** The chip beside the hovered object, where it does not cover the object's own label, kept inside the layer. */
function Chip({ picked }: { picked: Picked }) {
  const chip = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = chip.current;
    const layer = element?.parentElement;
    if (!element || !layer) return;
    const { left, top } = placeChip(
      picked.box,
      { width: element.offsetWidth, height: element.offsetHeight },
      { width: layer.clientWidth, height: layer.clientHeight },
    );
    element.style.left = `${left}px`;
    element.style.top = `${top}px`;
  }, [picked]);
  return (
    <div
      ref={chip}
      role="tooltip"
      className="absolute flex max-w-full flex-col rounded-control bg-canvas-scrim px-m py-xs text-small text-on-canvas"
    >
      <span className="truncate font-semibold">{picked.label}</span>
      {picked.detail && <span className="truncate text-on-canvas-muted">{picked.detail}</span>}
    </div>
  );
}

/**
 * The hover chip and the outlines of the hovered and the selected objects, laid over the canvas as plain elements:
 * UI, so never part of an export. While `objectDetails` is off it shows nothing.
 */
export function ObjectLayer({
  selection,
  objectDetails,
  handle,
}: {
  selection: Selection;
  objectDetails: boolean;
  handle: Ref<ObjectLayerHandle>;
}) {
  const geometry = useRef<Geometry | null>(null);
  const hovered = useRef<Picked | null>(null);
  const latest = useRef({ selection, objectDetails });
  const [outlines, setOutlines] = useState<readonly Outline[]>(NO_OUTLINES);
  const [chip, setChip] = useState<Picked | null>(null);

  const refresh = () => {
    const { selection: chosen, objectDetails: on } = latest.current;
    const next = (on && geometry.current?.boxes(chosen, hovered.current)) || NO_OUTLINES;
    setOutlines((now) => (sameOutlines(now, next) ? now : next));
  };
  useImperativeHandle(handle, () => ({
    hover(picked) {
      hovered.current = latest.current.objectDetails ? picked : null;
      setChip(hovered.current);
      refresh();
    },
    drawn(next) {
      geometry.current = next;
      refresh();
    },
  }));
  useEffect(() => {
    latest.current = { selection, objectDetails };
    if (!objectDetails) {
      hovered.current = null;
      setChip(null);
    }
    refresh();
  }, [selection, objectDetails]);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {outlines.map(({ lane, rect, kind }, i) => (
        <div
          key={`${lane}:${kind}:${i}`}
          data-outline={kind}
          className={`absolute outline outline-canvas ${kind === "selected" ? "border-2 border-on-canvas" : "border border-on-canvas-muted"}`}
          style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }}
        />
      ))}
      {chip && <Chip picked={chip} />}
    </div>
  );
}
