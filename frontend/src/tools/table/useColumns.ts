import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from "react";
import type { Column } from "./columns";
import { dragWidth, fitColumns, fitWidth, rememberedWidths, type Fit, type Measure } from "./fit";
import { fontsOf, measureColumns, type Fonts } from "./measure";

/** A resize in progress: where the pointer went down, and the widths as they stand until it is let go. */
type Drag = {
  index: number;
  x: number;
  start: { width: number; sized: boolean };
  draft: Map<string, number>;
};

/** What the last fit was made of, for the dividers. */
type Applied = Fit & { measures: Measure[]; available: number; fonts: Fonts | null };

const same = (a: readonly number[], b: readonly number[]): boolean =>
  a.length === b.length && a.every((value, k) => value === b[k]);

/**
 * Decides which columns of a table are shown and how wide, and applies it. The widths are written to the CSS variable
 * `--cols` of `root` without rendering again; only a change in which columns are shown renders. `rows` are the ones in
 * view, measured on each render; `scroller` and `header` give the room and the faces. The dividers' handlers resize a
 * column by pointer or keys and fit it to its content by double click; the widths the user gives are remembered for
 * the session under `tableKey`.
 */
export function useColumns<Row>(
  tableKey: string,
  columns: readonly Column<Row>[],
  rows: readonly (Row | undefined)[],
  scroller: RefObject<HTMLElement | null>,
) {
  const root = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLDivElement>(null);
  const remembered = useMemo(() => rememberedWidths(tableKey), [tableKey]);
  const [shown, setShown] = useState<readonly number[]>(() => columns.map((_, i) => i));
  const current = useRef({ columns, rows });
  const applied = useRef<Applied>({
    shown: [],
    widths: [],
    measures: [],
    available: 0,
    fonts: null,
  });
  const drag = useRef<Drag | null>(null);

  const refit = useCallback(() => {
    const box = root.current;
    const list = scroller.current;
    const head = header.current;
    if (!box || !list || !head) return;
    const fonts = fontsOf(list, head);
    const style = getComputedStyle(head);
    const room =
      list.clientWidth -
      parseFloat(style.paddingLeft) -
      parseFloat(style.paddingRight) -
      parseFloat(style.borderLeftWidth);
    const available = Number.isFinite(room) ? room : list.clientWidth;
    if (available <= 0) return;
    const { columns: cols, rows: inView } = current.current;
    const measures = measureColumns(cols, inView, fonts);
    const fit = fitColumns(cols, measures, available, drag.current?.draft ?? remembered);
    const before = applied.current;
    applied.current = { ...fit, measures, available, fonts };
    if (!same(fit.widths, before.widths) || !same(fit.shown, before.shown))
      box.style.setProperty("--cols", fit.widths.map((width) => `${width}px`).join(" "));
    box.querySelectorAll<HTMLElement>('[role="separator"]').forEach((divider, k) => {
      const index = fit.shown[k];
      if (index === undefined) return;
      const values = {
        "aria-valuenow": Math.round(fit.widths[k]),
        "aria-valuemin": Math.round(measures[index].least),
        "aria-valuemax": Math.round(available),
      };
      for (const [name, value] of Object.entries(values))
        if (divider.getAttribute(name) !== String(value)) divider.setAttribute(name, String(value));
    });
    setShown((now) => (same(now, fit.shown) ? now : fit.shown));
  }, [scroller, remembered]);

  // After every render: the rows in view may have changed what the columns need.
  useLayoutEffect(() => {
    current.current = { columns, rows };
    refit();
  });
  useLayoutEffect(() => {
    const box = root.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(refit);
    observer.observe(box);
    return () => observer.disconnect();
  }, [refit]);

  /** Resizes the column at `index` towards `requested` pixels, remembering what could be applied in `into`. */
  const resize = (
    index: number,
    requested: number,
    into: Map<string, number>,
    start: Drag["start"],
  ) => {
    const { shown: visible, measures, available } = applied.current;
    const title = current.current.columns[index].title;
    const width = dragWidth(
      current.current.columns,
      measures,
      into,
      visible,
      index,
      requested,
      available,
      start,
    );
    if (width === null) into.delete(title);
    else into.set(title, width);
    refit();
  };

  /** The handlers of the divider after the column at `index`. */
  const divider = (index: number) => {
    const title = columns[index].title;
    const widthOf = () => applied.current.widths[applied.current.shown.indexOf(index)];
    return {
      onPointerDown: (event: PointerEvent<HTMLElement>) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {
          index,
          x: event.clientX,
          start: { width: widthOf(), sized: remembered.has(title) },
          draft: new Map(remembered),
        };
      },
      onPointerMove: (event: PointerEvent<HTMLElement>) => {
        const now = drag.current;
        if (now?.index === index)
          resize(index, now.start.width + event.clientX - now.x, now.draft, now.start);
      },
      onPointerUp: () => {
        const now = drag.current;
        if (now?.index !== index) return;
        const width = now.draft.get(title);
        if (width === undefined) remembered.delete(title);
        else remembered.set(title, width);
        drag.current = null;
      },
      onDoubleClick: () => {
        remembered.set(title, fitWidth(applied.current.measures[index]));
        refit();
      },
      onKeyDown: (event: KeyboardEvent) => {
        const step = Math.round((applied.current.fonts?.body.ch ?? 8) * 2);
        const by = event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0;
        if (by === 0) return;
        event.preventDefault();
        const width = widthOf();
        resize(index, width + by, remembered, { width, sized: remembered.has(title) });
      },
    };
  };

  return { root, header, shown, divider };
}
