import { useEffect, useRef, useState } from "react";
import { RendererUnavailable, VideoRenderer } from "./renderer";
import type { PlaybackSession } from "./session";

/** The size in device pixels that a resize observation reports. */
function devicePixels(entry: ResizeObserverEntry): [number, number] {
  const exact = entry.devicePixelContentBoxSize?.[0];
  if (exact) return [exact.inlineSize, exact.blockSize];
  return [
    Math.round(entry.contentRect.width * devicePixelRatio),
    Math.round(entry.contentRect.height * devicePixelRatio),
  ];
}

/**
 * The picture the session presents, fitted into the space it is given and drawn at the screen's pixel density. A
 * browser without WebGL2 says so instead. `anchor` is the `data-cursor-id` of an element laid exactly over the
 * picture, not the bars around it, so another window's pointer lands on the same image pixel at any window size.
 */
export function VideoCanvas({
  session,
  label,
  anchor,
}: {
  session: PlaybackSession;
  label: string;
  anchor: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [picture, setPicture] = useState<{ width: number; height: number } | null>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    let renderer: VideoRenderer;
    try {
      renderer = new VideoRenderer(element);
    } catch (error) {
      if (error instanceof RendererUnavailable) return setUnavailable(error.message);
      throw error;
    }
    // The picture's size comes with its frames; the renderer fits it into the canvas.
    const attach = (drawing: VideoRenderer) =>
      session.setSurface({
        present: (frame) => {
          drawing.present(frame);
          const { width, height } = frame;
          setPicture((now) =>
            now?.width === width && now.height === height ? now : { width, height },
          );
        },
      });
    attach(renderer);
    const observer = new ResizeObserver(([entry]) => {
      const [width, height] = devicePixels(entry);
      renderer.resize(width, height);
    });
    observer.observe(element, { box: "device-pixel-content-box" });
    // A lost graphics context loses the picture: draw the frame on screen again once it is back.
    const lost = (event: Event) => event.preventDefault();
    const restored = () => {
      renderer = new VideoRenderer(element);
      attach(renderer);
      renderer.resize(element.width, element.height);
      session.represent();
    };
    element.addEventListener("webglcontextlost", lost);
    element.addEventListener("webglcontextrestored", restored);
    return () => {
      element.removeEventListener("webglcontextlost", lost);
      element.removeEventListener("webglcontextrestored", restored);
      observer.disconnect();
      session.setSurface(null);
      renderer.dispose();
    };
  }, [session]);
  if (unavailable)
    return (
      <p role="alert" className="m-auto p-xl">
        The video cannot be drawn: {unavailable}
      </p>
    );
  return (
    <>
      <canvas ref={canvas} role="img" aria-label={label} className="absolute inset-0 size-full" />
      {picture && (
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
      )}
    </>
  );
}
