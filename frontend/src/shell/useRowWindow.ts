import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Rows drawn beyond the visible ones on each side, so scrolling never shows a gap. */
const OVERSCAN = 8;
/** Rows drawn before the first measurement, enough to measure one and fill any sidebar. */
const FIRST_PAINT = 60;

type Measure = { top: number; height: number; row: number };

/**
 * Which of `count` equal-height rows a scroll container has to draw. Attach `ref` to the container; the rows are
 * `[start, end)` and the caller pads the container by the rows left out. A row's height is measured from the first
 * element matching `selector` (tree items by default) in the container, so it follows the zoom.
 */
export function useRowWindow(count: number, selector = '[role="treeitem"]') {
  const ref = useRef<HTMLDivElement>(null);
  const [measure, setMeasure] = useState<Measure>({ top: 0, height: 0, row: 0 });

  const update = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    const next = {
      top: element.scrollTop,
      height: element.clientHeight,
      row: element.querySelector<HTMLElement>(selector)?.offsetHeight ?? 0,
    };
    setMeasure((now) =>
      now.top === next.top && now.height === next.height && now.row === next.row ? now : next,
    );
  }, [selector]);

  useLayoutEffect(update);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [update]);

  const { top, height, row } = measure;
  const measured = row > 0 && height > 0;
  const start = measured ? Math.max(0, Math.floor(top / row) - OVERSCAN) : 0;
  const end = measured
    ? Math.min(count, Math.ceil((top + height) / row) + OVERSCAN)
    : Math.min(count, FIRST_PAINT);

  /** Scrolls the container just far enough to bring row `index` into view. */
  const reveal = (index: number) => {
    const element = ref.current;
    if (!element || !measured) return;
    if (index * row < element.scrollTop) element.scrollTop = index * row;
    else if ((index + 1) * row > element.scrollTop + height)
      element.scrollTop = (index + 1) * row - height;
    update();
  };

  return { ref, start, end, update, reveal };
}
