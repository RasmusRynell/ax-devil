import { useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from "react";
import { elideMiddle, GAP, LABEL_CEILING, labelRoom, MARGIN, placeChip } from "./chipPlacement";
import type { Picked, Selection } from "./contract";
import type { Geometry, Outline } from "./renderer";

/** How the canvas reports to the layer; the layer renders by itself, so the viewer does not render per frame. */
export type ObjectLayerHandle = {
  /** The object under the pointer changed (`null`: none). */
  hover(picked: Picked | null): void;
  /** The pictures were drawn: outlines follow the objects. */
  drawn(geometry: Geometry): void;
};

/** Stroke width as a fraction of the picture's shorter side; the selected object reads heavier than the hovered one. */
const STROKE = { hover: 0.004, selected: 0.009 };

const NO_OUTLINES: readonly Outline[] = [];

const sameOutlines = (a: readonly Outline[], b: readonly Outline[]): boolean =>
  a.length === b.length &&
  a.every(
    (outline, i) =>
      outline.lane === b[i].lane &&
      outline.kind === b[i].kind &&
      outline.scale === b[i].scale &&
      outline.rect.x === b[i].rect.x &&
      outline.rect.y === b[i].rect.y &&
      outline.rect.width === b[i].rect.width &&
      outline.rect.height === b[i].rect.height,
  );

/** The dot's color by `Picked.category`, written out so the stylesheet generates them. */
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

/**
 * The chip beside the hovered object, as the reference shows it: a dot in the class's color, the id and class, and the
 * facts. It sits on the side of the object with the most room, kept inside the layer. The id and class are elided in
 * the middle and the facts at the end when the layer is too narrow for them.
 */
function Chip({ picked }: { picked: Picked }) {
  const chip = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLSpanElement>(null);
  const detail = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const element = chip.current;
    const name = label.current;
    const facts = detail.current;
    const layer = element?.parentElement;
    if (!element || !name || !layer) return;
    // The text at its full length first, then fitted into the layer.
    if (facts) facts.style.maxWidth = "";
    name.textContent = "0".repeat(LABEL_CEILING);
    const ceiling = name.offsetWidth;
    name.textContent = picked.label;
    const factsWidth = facts?.offsetWidth ?? 0;
    const gap = facts ? GAP : 0;
    const chrome = element.offsetWidth - name.offsetWidth - factsWidth - gap;
    const room = layer.clientWidth - 2 * MARGIN - chrome;
    const wanted = labelRoom(room, factsWidth, ceiling);
    name.textContent = elideMiddle(picked.label, (text) => {
      name.textContent = text;
      return name.offsetWidth <= wanted;
    });
    if (facts) facts.style.maxWidth = `${Math.max(0, room - name.offsetWidth - GAP)}px`;
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
      className="absolute flex w-max items-center gap-s rounded-control border border-canvas-chip-line bg-canvas-chip p-s whitespace-nowrap"
    >
      <span aria-hidden className={`size-m shrink-0 rounded-full ${DOTS[picked.category]}`} />
      <span ref={label} className="font-mono text-aside text-on-canvas">
        {picked.label}
      </span>
      {picked.detail && (
        <span
          ref={detail}
          className="ml-s overflow-hidden text-small text-ellipsis text-on-canvas-muted"
        >
          {picked.detail}
        </span>
      )}
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
      {outlines.map(({ lane, rect, kind, scale }, i) => (
        <div
          key={`${lane}:${kind}:${i}`}
          data-outline={kind}
          className={`absolute border-solid border-on-canvas ${kind === "hover" ? "bg-on-canvas/12" : ""}`}
          style={{
            left: rect.x,
            top: rect.y,
            width: rect.width,
            height: rect.height,
            borderWidth: STROKE[kind] * scale,
          }}
        />
      ))}
      {chip && <Chip picked={chip} />}
    </div>
  );
}
