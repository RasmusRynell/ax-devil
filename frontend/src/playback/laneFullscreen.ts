import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/**
 * Lane fullscreen of a viewer pane: the pane goes fullscreen showing one lane (`solo`), so the transport, tools and
 * inspector come along, and the other lanes keep playing. `point` and `pick` are told which lane the pointer is over
 * and which was last pressed; `focus` names one by keyboard and then outranks where the pointer was, until it moves or
 * presses again. `toggle` takes the lane under the pointer, else the last pressed, else the first of `shown`. Leaving
 * fullscreen by any route clears `solo` and puts the keyboard back on the pane; unmounting or replacing
 * the optional playback `lifetime` leaves it.
 */
export function useLaneFullscreen(
  pane: RefObject<HTMLElement | null>,
  shown: readonly number[],
  lifetime?: object,
) {
  const [solo, setSolo] = useState<number | null>(null);
  const aim = useRef<{ pointer: number | null; clicked: number | null; shown: readonly number[] }>({
    pointer: null,
    clicked: null,
    shown,
  });
  useEffect(() => {
    aim.current.shown = shown;
  });

  const point = useCallback((lane: number | null) => void (aim.current.pointer = lane), []);
  const pick = useCallback((lane: number) => void (aim.current.clicked = lane), []);
  /** Puts the keyboard on `lane` ahead of where the pointer last was, until the pointer moves or presses again. */
  const focus = useCallback((lane: number) => {
    aim.current.pointer = null;
    aim.current.clicked = lane;
  }, []);
  /** The lane the keyboard and the lane menu act on, or null while no lane is shown. */
  const target = useCallback((): number | null => {
    const { pointer, clicked, shown: lanes } = aim.current;
    const found = [pointer, clicked].find((lane) => lane !== null && lanes.includes(lane));
    return found ?? lanes[0] ?? null;
  }, []);
  const toggle = useCallback(() => {
    const element = pane.current;
    if (!element) return;
    if (document.fullscreenElement === element) return void document.exitFullscreen();
    const lane = target();
    if (lane === null) return;
    setSolo(lane);
    element.requestFullscreen().catch(() => setSolo(null));
  }, [pane, target]);
  /** Leaves fullscreen if the pane is in it; true when it was, so the key that asked has done its job. */
  const leave = useCallback(() => {
    if (document.fullscreenElement !== pane.current) return false;
    void document.exitFullscreen();
    return true;
  }, [pane]);

  useEffect(() => {
    const element = pane.current;
    setSolo(null);
    let inside = false;
    const changed = () => {
      const now = document.fullscreenElement === element;
      if (inside && !now) {
        setSolo(null);
        element?.focus({ preventScroll: true });
      }
      inside = now;
    };
    document.addEventListener("fullscreenchange", changed);
    return () => {
      document.removeEventListener("fullscreenchange", changed);
      if (document.fullscreenElement === element) void document.exitFullscreen();
    };
  }, [pane, lifetime]);

  return { solo, point, pick, focus, target, toggle, leave };
}
