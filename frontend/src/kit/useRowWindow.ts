import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Rows drawn beyond the visible ones on each side, so scrolling never shows a gap. */
const OVERSCAN = 8;
/** Rows drawn before the first measurement, enough to measure one and fill any sidebar. */
const FIRST_PAINT = 60;

type Measure = { top: number; height: number; row: number };
type Range = { start: number; end: number };

const UNMEASURED: Measure = { top: 0, height: 0, row: 0 };

const rangeOf = ({ top, height, row }: Measure, count: number): Range =>
  row > 0 && height > 0
    ? {
        start: Math.max(0, Math.floor(top / row) - OVERSCAN),
        end: Math.min(count, Math.ceil((top + height) / row) + OVERSCAN),
      }
    : { start: 0, end: Math.min(count, FIRST_PAINT) };

/**
 * Which of `count` equal-height rows a scroll container has to draw. Attach `ref` to the container; the rows are
 * `[start, end)` and the caller pads the container by the rows left out. A row's height is measured from the first
 * element matching `selector` (tree items by default) in the container, so it follows the zoom. The measurement
 * lives in a ref: the caller renders again only when the rows to draw change.
 */
export function useRowWindow(count: number, selector = '[role="treeitem"]') {
  const ref = useRef<HTMLDivElement>(null);
  const measure = useRef<Measure>(UNMEASURED);
  const rows = useRef(count);
  const [range, setRange] = useState<Range>(() => rangeOf(UNMEASURED, count));

  const update = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    measure.current = {
      top: element.scrollTop,
      height: element.clientHeight,
      row: element.querySelector<HTMLElement>(selector)?.offsetHeight ?? 0,
    };
    const next = rangeOf(measure.current, rows.current);
    setRange((now) => (now.start === next.start && now.end === next.end ? now : next));
  }, [selector]);

  // Rows come and go with the count; until a row has been measured, every render tries again.
  useLayoutEffect(() => {
    rows.current = count;
    update();
  }, [count, update]);
  useLayoutEffect(() => {
    if (measure.current.row === 0) update();
  });
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [update]);

  const start = Math.min(range.start, count);
  const end = Math.min(range.end, count);

  /** Scrolls the container just far enough to bring row `index` into view. */
  const reveal = (index: number) => {
    const element = ref.current;
    const { row, height } = measure.current;
    if (!element || row === 0 || height === 0) return;
    if (index * row < element.scrollTop) element.scrollTop = index * row;
    else if ((index + 1) * row > element.scrollTop + height)
      element.scrollTop = (index + 1) * row - height;
    update();
  };

  return { ref, start, end, update, reveal };
}
