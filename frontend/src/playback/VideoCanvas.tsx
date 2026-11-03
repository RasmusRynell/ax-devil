import { useEffect, useRef, useState } from "react";
import type { Picked, Selection, Viewport } from "./contract";
import {
  RendererUnavailable,
  VideoRenderer,
  type CellRect,
  type Geometry,
  type LaneCell,
  type Point,
} from "./renderer";
import type { PlaybackSession } from "./session";
import { FIT, ZOOM_STEP, pictureRect } from "./viewport";

/** A pointer that moves farther than this many CSS pixels between press and release dragged; it did not click. */
const CLICK_SLOP = 3;

/** Whether two hovered objects are the same object in the same place. */
function samePicked(a: Picked | null, b: Picked | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.lane === b.lane &&
    a.source === b.source &&
    a.sample === b.sample &&
    a.row === b.row &&
    a.entity === b.entity &&
    a.box.x === b.box.x &&
    a.box.y === b.box.y &&
    a.box.width === b.box.width &&
    a.box.height === b.box.height
  );
}

/** Where the cells are, as text, to tell a layout that moved from one that was merely rebuilt. */
function cellsKey(cells: readonly LaneLayout[] | undefined): string {
  return (cells ?? [])
    .map(({ lane, rect }) => `${lane}:${rect.x},${rect.y},${rect.width},${rect.height}`)
    .join(" ");
}

/** The object clicked, as a selection. */
function selectionOf(picked: Picked | null): Selection {
  return picked && { lane: picked.lane, source: picked.source, entity: picked.entity };
}

/** Where a lane is drawn, in CSS pixels from the canvas's top-left corner. */
export type LaneLayout = { lane: number; rect: CellRect };

/** The size in device pixels that a resize observation reports. */
function devicePixels(entry: ResizeObserverEntry): [number, number] {
  const exact = entry.devicePixelContentBoxSize?.[0];
  if (exact) return [exact.inlineSize, exact.blockSize];
  return [
    Math.round(entry.contentRect.width * devicePixelRatio),
    Math.round(entry.contentRect.height * devicePixelRatio),
  ];
}

/** The cells in device pixels of the canvas as it is now, edges rounded so neighbouring cells share them. */
function deviceCells(
  cells: readonly LaneLayout[] | undefined,
  canvas: HTMLCanvasElement,
): LaneCell[] | null {
  if (!cells) return null;
  const across = canvas.width / Math.max(1, canvas.clientWidth);
  const down = canvas.height / Math.max(1, canvas.clientHeight);
  return cells.map(({ lane, rect }) => {
    const x = Math.round(rect.x * across);
    const y = Math.round(rect.y * down);
    return {
      lane,
      rect: {
        x,
        y,
        width: Math.round((rect.x + rect.width) * across) - x,
        height: Math.round((rect.y + rect.height) * down) - y,
      },
    };
  });
}

/**
 * Elements laid exactly over the picture, one per cell, for other windows' pointers: a pointer lands on the same
 * image pixel at any window size, whatever the bars around the picture. Without `cells` one fills the space.
 */
function Anchors({
  anchor,
  picture,
  cells,
  viewport,
}: {
  anchor: string;
  picture: { width: number; height: number };
  cells: readonly LaneLayout[] | undefined;
  viewport: Viewport;
}) {
  if (!cells)
    return (
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex items-center justify-center [container-type:size]"
      >
        <div
          data-cursor-id={anchor}
          className="pointer-events-auto"
          style={{
            aspectRatio: `${picture.width} / ${picture.height}`,
            width: `min(100cqw, calc(100cqh * ${picture.width} / ${picture.height}))`,
          }}
        />
      </div>
    );
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {cells.map(({ lane, rect }) => {
        const shown = pictureRect(rect, picture, viewport);
        // A zoomed picture reaches past its cell; the part outside answers to no pointer, and the surface's clip keeps
        // it from adding to the page's scrollable area.
        const cut = (outside: number) => Math.max(0, outside);
        const clip = [
          cut(rect.y - shown.y),
          cut(shown.x + shown.width - rect.x - rect.width),
          cut(shown.y + shown.height - rect.y - rect.height),
          cut(rect.x - shown.x),
        ];
        return (
          <div
            key={lane}
            data-cursor-id={`${anchor}:${lane}`}
            className="pointer-events-auto absolute"
            style={{
              left: shown.x,
              top: shown.y,
              width: shown.width,
              height: shown.height,
              clipPath: clip.some(Boolean)
                ? `inset(${clip.map((px) => `${px}px`).join(" ")})`
                : undefined,
            }}
          />
        );
      })}
    </div>
  );
}

/**
 * The picture the session presents, drawn at the screen's pixel density into each lane's cell with that lane's
 * marks over it; without `cells`, one cell for lane 0 fills the space. A browser without WebGL2 says so instead.
 * `anchor` names the `data-cursor-id` of the elements over the pictures; with `cells` each gets `:<lane>` after it.
 *
 * `viewport` zooms and pans every picture; the wheel zooms toward the pointer, a drag pans a zoomed picture and a
 * double click resets, each asking for the new viewport through `onViewport`. While `objectDetails` is on, the
 * object under the pointer is reported to `onHover` (null: none), a click that was not a drag to `onSelect`
 * (null: empty space), and `onDrawn` gets the geometry of the pictures after every draw. As in the reference, the
 * hovered object is dropped when the canvas resizes, zooms or pans, or the pointer leaves, and found again when the
 * pointer moves or the next picture arrives.
 */
export function VideoCanvas({
  session,
  label,
  anchor,
  cells,
  viewport = FIT,
  onViewport,
  objectDetails = true,
  onHover,
  onSelect,
  onDrawn,
}: {
  session: PlaybackSession;
  label: string;
  anchor: string;
  cells?: readonly LaneLayout[];
  viewport?: Viewport;
  onViewport?: (viewport: Viewport) => void;
  objectDetails?: boolean;
  onHover?: (picked: Picked | null) => void;
  onSelect?: (selection: Selection) => void;
  onDrawn?: (geometry: Geometry) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<VideoRenderer | null>(null);
  const layout = useRef(cells);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [picture, setPicture] = useState<{ width: number; height: number } | null>(null);
  // What the handlers below need to be current without registering them again.
  const live = useRef({ objectDetails, onViewport, onHover, onSelect, onDrawn });
  const shown = useRef(viewport);
  const hovered = useRef<Picked | null>(null);
  const pointer = useRef<Point | null>(null);
  /** Whether the pointer may be over an object; the moves and pictures that follow a drop set it again. */
  const armed = useRef(false);
  const placed = useRef(cellsKey(cells));
  const press = useRef<{ start: Point; last: Point; dragged: boolean } | null>(null);
  /** Whether the last press moved far enough to be a drag, which the click after it must not count as. */
  const dragged = useRef(false);

  const pointAt = (event: { clientX: number; clientY: number }): Point => {
    const at = (box.current as HTMLDivElement).getBoundingClientRect();
    return { x: event.clientX - at.left, y: event.clientY - at.top };
  };
  const hover = () => {
    const at = pointer.current;
    const picking = armed.current && live.current.objectDetails && at && !press.current?.dragged;
    const next = (picking && renderer.current?.pick(at.x, at.y)) || null;
    if (samePicked(hovered.current, next)) return;
    hovered.current = next;
    live.current.onHover?.(next);
  };
  /** Drops the hovered object until the pointer moves or a picture arrives. */
  const drop = () => {
    armed.current = false;
    hover();
  };
  const change = (next: Viewport) => {
    if (next === shown.current) return;
    shown.current = next;
    live.current.onViewport?.(next);
  };

  useEffect(() => {
    live.current = { objectDetails, onViewport, onHover, onSelect, onDrawn };
    hover();
  });
  useEffect(() => {
    shown.current = viewport;
    renderer.current?.setViewport(viewport);
    drop();
  }, [viewport]);
  useEffect(() => {
    layout.current = cells;
    const element = canvas.current;
    if (element) renderer.current?.setCells(deviceCells(cells, element));
    if (placed.current !== cellsKey(cells)) {
      placed.current = cellsKey(cells);
      drop();
    }
  }, [cells]);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    let current: VideoRenderer;
    // The picture's size comes with its frames; the renderer fits it into each cell.
    const attach = () => {
      current = new VideoRenderer(element);
      renderer.current = current;
      current.onDrawn = () => {
        hover();
        live.current.onDrawn?.(current);
      };
      current.setViewport(shown.current);
      current.setCells(deviceCells(layout.current, element));
      session.setSurface({
        present: (frame, lanes) => {
          armed.current = true;
          current.present(frame, lanes);
          const { width, height } = frame;
          setPicture((now) =>
            now?.width === width && now.height === height ? now : { width, height },
          );
        },
      });
    };
    try {
      attach();
    } catch (error) {
      if (error instanceof RendererUnavailable) return setUnavailable(error.message);
      throw error;
    }
    const observer = new ResizeObserver(([entry]) => {
      const [width, height] = devicePixels(entry);
      drop();
      current.resize(width, height);
      current.setCells(deviceCells(layout.current, element));
    });
    observer.observe(element, { box: "device-pixel-content-box" });
    // Page zoom changes the pixel density without always changing the canvas's device size; overlays follow it.
    let density: MediaQueryList;
    const redrawOnDensity = () => {
      drop();
      current.redraw();
      watchDensity();
    };
    const watchDensity = () => {
      density?.removeEventListener("change", redrawOnDensity);
      density = matchMedia(`(resolution: ${devicePixelRatio}dppx)`);
      density.addEventListener("change", redrawOnDensity, { once: true });
    };
    watchDensity();
    // A lost graphics context loses the picture: draw the frame on screen again once it is back.
    const lost = (event: Event) => event.preventDefault();
    const restored = () => {
      attach();
      current.resize(element.width, element.height);
      session.represent();
    };
    // Not a React handler, which is passive and could not keep the page from zooming under the pointer.
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const lines = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 33 : 1;
      const notches = Math.max(-1, Math.min(1, (-event.deltaY * lines) / 100));
      drop();
      change(current.zoomAt(shown.current, ZOOM_STEP ** notches, pointAt(event)));
    };
    const surface = box.current as HTMLDivElement;
    surface.addEventListener("wheel", wheel, { passive: false });
    element.addEventListener("webglcontextlost", lost);
    element.addEventListener("webglcontextrestored", restored);
    return () => {
      surface.removeEventListener("wheel", wheel);
      element.removeEventListener("webglcontextlost", lost);
      element.removeEventListener("webglcontextrestored", restored);
      density.removeEventListener("change", redrawOnDensity);
      observer.disconnect();
      session.setSurface(null);
      renderer.current = null;
      current.onDrawn = null;
      current.dispose();
    };
  }, [session]);
  if (unavailable)
    return (
      <p role="alert" className="m-auto p-xl">
        The video cannot be drawn: {unavailable}
      </p>
    );
  return (
    <div
      ref={box}
      className={`absolute inset-0 touch-none overflow-clip ${viewport.zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""}`}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        const at = pointAt(event);
        press.current = { start: at, last: at, dragged: false };
        dragged.current = false;
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const at = pointAt(event);
        pointer.current = at;
        const held = press.current;
        if (!held) {
          armed.current = true;
          return hover();
        }
        if (!held.dragged && Math.hypot(at.x - held.start.x, at.y - held.start.y) <= CLICK_SLOP)
          return;
        held.dragged = true;
        dragged.current = true;
        change(
          renderer.current?.panBy(
            shown.current,
            held.start,
            at.x - held.last.x,
            at.y - held.last.y,
          ) ?? shown.current,
        );
        held.last = at;
        drop();
      }}
      onPointerUp={(event) => {
        if (!press.current) return;
        press.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onClick={(event) => {
        // The second click of a double click resets the viewport; it selects nothing more.
        if (dragged.current || event.detail > 1 || !live.current.objectDetails) return;
        const at = pointAt(event);
        live.current.onSelect?.(selectionOf(renderer.current?.pick(at.x, at.y) ?? null));
      }}
      onPointerCancel={() => (press.current = null)}
      onPointerLeave={() => {
        pointer.current = null;
        hover();
      }}
      onDoubleClick={() => change(FIT)}
    >
      <canvas ref={canvas} role="img" aria-label={label} className="absolute inset-0 size-full" />
      {picture && <Anchors anchor={anchor} picture={picture} cells={cells} viewport={viewport} />}
    </div>
  );
}
