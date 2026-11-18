import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** Rows drawn beyond the visible ones on each side, so scrolling never shows a gap. */
const OVERSCAN = 8;
/** Rows drawn before the first measurement, enough to measure one and fill any sidebar. */
const FIRST_PAINT = 60;

type Span = { start: number; end: number; before: number; after: number };

const same = (a: Span, b: Span) =>
  a.start === b.start && a.end === b.end && a.before === b.before && a.after === b.after;

/**
 * Which of the rows `ids` a list has to draw when the rows differ in height, inside a scrolling `scroller` that
 * holds the list and more. Attach `ref` to the list; the rows are `[start, end)` and the list is padded by `before`
 * and `after`, the height of the rows left out. A row is measured once drawn (it carries `data-span`, set to its id), and
 * until then counts as the least height measured, so the heights follow the zoom. Measurements live in refs: the
 * caller renders again only when the rows to draw change.
 */
export function useSpanWindow(ids: readonly string[], scroller: RefObject<HTMLElement | null>) {
  const ref = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const least = useRef(0);
  const rows = useRef(ids);
  const [span, setSpan] = useState<Span>({
    start: 0,
    end: Math.min(ids.length, FIRST_PAINT),
    before: 0,
    after: 0,
  });

  const heightOf = (id: string | undefined) => (id && heights.current.get(id)) || least.current;

  const update = useCallback(() => {
    const list = ref.current;
    const box = scroller.current;
    if (!list || !box) return;
    const measured = [...list.querySelectorAll<HTMLElement>("[data-span]")].map(
      (element) => [element.dataset.span ?? "", element.getBoundingClientRect().height] as const,
    );
    measured.forEach(([id, height]) => heights.current.set(id, height));
    const smallest = Math.min(
      ...measured.map(([, height]) => height).filter((height) => height > 0),
    );
    least.current = Number.isFinite(smallest) ? smallest : least.current;
    const all = rows.current;
    const first: Span = { start: 0, end: Math.min(all.length, FIRST_PAINT), before: 0, after: 0 };
    if (least.current === 0 || box.clientHeight === 0) {
      setSpan((now) => (same(now, first) ? now : first));
      return;
    }
    // The viewport in the list's own coordinates, whose top edge is where the first row starts.
    const seen = box.getBoundingClientRect().top - list.getBoundingClientRect().top;
    const low = seen - OVERSCAN * least.current;
    const high = seen + box.clientHeight + OVERSCAN * least.current;
    let start = 0;
    let before = 0;
    while (start < all.length && before + heightOf(all[start]) <= low)
      before += heightOf(all[start++]);
    let end = start;
    let bottom = before;
    while (end < all.length && bottom < high) bottom += heightOf(all[end++]);
    let after = 0;
    for (let at = end; at < all.length; at++) after += heightOf(all[at]);
    const next = { start, end, before, after };
    setSpan((now) => (same(now, next) ? now : next));
  }, [scroller]);

  // Rows come and go and change height with each render; measure what is drawn.
  useLayoutEffect(() => {
    rows.current = ids;
    update();
  });
  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    box.addEventListener("scroll", update, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(box);
    return () => {
      box.removeEventListener("scroll", update);
      observer?.disconnect();
    };
  }, [scroller, update]);

  /** Scrolls the scroller just far enough to bring row `index` into view, drawn or not. */
  const reveal = (index: number) => {
    const list = ref.current;
    const box = scroller.current;
    if (!list || !box || least.current === 0) return;
    const origin =
      list.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    let top = origin;
    for (let at = 0; at < index; at++) top += heightOf(rows.current[at]);
    const bottom = top + heightOf(rows.current[index]);
    if (top < box.scrollTop) box.scrollTop = top;
    else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
    update();
  };

  const start = Math.min(span.start, ids.length);
  const end = Math.min(span.end, ids.length);
  return { ref, start, end, before: span.before, after: span.after, reveal };
}
