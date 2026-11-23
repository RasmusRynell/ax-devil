/**
 * A list that follows a line down its rows: the line between the events that have happened and those still to come.
 * The list scrolls to keep the line in view while it follows, stops following when the user scrolls the line out of
 * view, and follows again when they bring it back. Programmatic scrolls never change that.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/** Where the line is and whether the list follows it. */
export type Follow = {
  /** The row the line lies before, so the rows above it have happened; `null` while it is unknown. */
  line: number | null;
  /** `third` keeps the latest row that happened a third of the way down the view; `end` keeps the end of the list in view. */
  keep: "third" | "end";
  following: boolean;
  onFollowing: (following: boolean) => void;
};

/** What a scroll container measures: how far it is scrolled, how tall its view is and how tall a row is. */
export type View = { top: number; height: number; row: number; rows: number };

/** Whether the line is in view: the row just above it is, whole. */
export function lineInView(line: number, { top, height, row }: View): boolean {
  const above = Math.max(line - 1, 0) * row;
  return top <= above && above <= top + height - row;
}

/** Where to scroll to bring a line that left the view back, or `null` when it is in view or nothing can be measured. */
export function scrollForLine(follow: Pick<Follow, "line" | "keep">, view: View): number | null {
  const { line, keep } = follow;
  if (line === null || view.row <= 0 || view.height <= 0 || lineInView(line, view)) return null;
  return keep === "end"
    ? Math.max(0, view.rows * view.row - view.height)
    : Math.max(0, Math.max(line - 1, 0) * view.row - Math.floor(view.height / 3));
}

const measure = (element: HTMLElement, rows: number): View => ({
  top: element.scrollTop,
  height: element.clientHeight,
  row: element.querySelector<HTMLElement>('[role="option"]')?.offsetHeight ?? 0,
  rows,
});

/**
 * Scrolls `ref` to `scrollTo`'s target without the list taking it for the user's doing, and follows the line: `onScroll`
 * (for the container's scroll event) turns following on or off by whether the line is in view, and the list scrolls to
 * the line whenever it moves or following begins while it is out of view.
 */
export function useLineFollow(ref: RefObject<HTMLElement | null>, rows: number, follow?: Follow) {
  // Whether the next scroll event is ours.
  const ours = useRef(false);
  const latest = useRef({ rows, follow });
  useLayoutEffect(() => {
    latest.current = { rows, follow };
  });

  const scrollTo = useCallback(
    (top: number) => {
      const element = ref.current;
      if (!element) return;
      const was = element.scrollTop;
      element.scrollTop = top;
      // A scroll that moved nothing raises no event, which would leave the flag to swallow the user's next one.
      if (element.scrollTop !== was) ours.current = true;
    },
    [ref],
  );

  const onScroll = useCallback(() => {
    if (ours.current) {
      ours.current = false;
      return;
    }
    const { rows, follow } = latest.current;
    const element = ref.current;
    if (!follow || !element || follow.line === null) return;
    const view = measure(element, rows);
    if (view.row <= 0) return;
    const on = lineInView(follow.line, view);
    if (on !== follow.following) follow.onFollowing(on);
  }, [ref]);

  const line = follow?.line ?? null;
  const following = follow?.following ?? false;
  useEffect(() => {
    const element = ref.current;
    if (!following || line === null || !element) return;
    const to = scrollForLine(
      { line, keep: latest.current.follow?.keep ?? "third" },
      measure(element, rows),
    );
    if (to !== null) scrollTo(to);
  }, [ref, line, following, rows, scrollTo]);

  return { scrollTo, onScroll };
}
