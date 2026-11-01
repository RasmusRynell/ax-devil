import { useEffect, useRef, useState } from "react";
import { RendererUnavailable, VideoRenderer, type CellRect, type LaneCell } from "./renderer";
import type { PlaybackSession } from "./session";

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
 * The picture the session presents, drawn at the screen's pixel density into each lane's cell with that lane's
 * overlay over it; without `cells`, one cell for lane 0 fills the space. A browser without WebGL2 says so instead.
 */
export function VideoCanvas({
  session,
  label,
  cells,
}: {
  session: PlaybackSession;
  label: string;
  cells?: readonly LaneLayout[];
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<VideoRenderer | null>(null);
  const layout = useRef(cells);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  useEffect(() => {
    layout.current = cells;
    const element = canvas.current;
    if (element) renderer.current?.setCells(deviceCells(cells, element));
  }, [cells]);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    let current: VideoRenderer;
    const attach = () => {
      current = new VideoRenderer(element);
      renderer.current = current;
      current.setCells(deviceCells(layout.current, element));
      session.setSurface(current);
    };
    try {
      attach();
    } catch (error) {
      if (error instanceof RendererUnavailable) return setUnavailable(error.message);
      throw error;
    }
    const observer = new ResizeObserver(([entry]) => {
      const [width, height] = devicePixels(entry);
      current.resize(width, height);
      current.setCells(deviceCells(layout.current, element));
    });
    observer.observe(element, { box: "device-pixel-content-box" });
    // A lost graphics context loses the picture: draw the frame on screen again once it is back.
    const lost = (event: Event) => event.preventDefault();
    const restored = () => {
      attach();
      current.resize(element.width, element.height);
      session.represent();
    };
    element.addEventListener("webglcontextlost", lost);
    element.addEventListener("webglcontextrestored", restored);
    return () => {
      element.removeEventListener("webglcontextlost", lost);
      element.removeEventListener("webglcontextrestored", restored);
      observer.disconnect();
      session.setSurface(null);
      renderer.current = null;
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
    <canvas ref={canvas} role="img" aria-label={label} className="absolute inset-0 size-full" />
  );
}
