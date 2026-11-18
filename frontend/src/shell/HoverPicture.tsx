import { useLayoutEffect, useRef } from "react";
import type { Shown } from "./useHoverPicture";

/** The longer side of the card in CSS pixels. */
const LONG_EDGE = 270;
/** The gap between the pointer and the card; the `m` spacing. */
const GAP = 8;

/** Down and right of the pointer, on its other side where that leaves the window, never past the window's top left. */
const placed = (at: number, size: number, limit: number) =>
  Math.max(0, at + GAP + size > limit ? at - GAP - size : at + GAP);

/**
 * A picture-only card by the pointer, at most `LONG_EDGE` on its longer side. It takes no focus and no pointer events,
 * and assistive technology skips it. The picture is drawn at the display's pixel ratio.
 */
export function HoverPicture({ bitmap, x, y }: Shown) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scale = LONG_EDGE / Math.max(bitmap.width, bitmap.height);
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  useLayoutEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return;
    element.width = Math.round(width * window.devicePixelRatio);
    element.height = Math.round(height * window.devicePixelRatio);
    context.imageSmoothingQuality = "high";
    context.drawImage(bitmap, 0, 0, element.width, element.height);
  }, [bitmap, width, height]);

  return (
    <div
      aria-hidden
      style={{
        left: placed(x, width, window.innerWidth),
        top: placed(y, height, window.innerHeight),
        width,
        height,
      }}
      className="pointer-events-none fixed z-20 overflow-hidden rounded-popup"
    >
      <canvas ref={canvas} className="block size-full" />
      <span className="absolute inset-0 rounded-popup border border-line-strong" />
    </div>
  );
}
