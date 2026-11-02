import { useEffect, useRef } from "react";

/** Swallows the click that completes the press that just happened, so what is under the pointer is not activated. */
function swallowNextClick() {
  const done = () => {
    document.removeEventListener("click", stop, true);
    document.removeEventListener("pointerup", release, true);
  };
  const stop = (event: Event) => {
    event.stopPropagation();
    event.preventDefault();
    done();
  };
  // A press that never becomes a click (a drag, a scroll bar) must not leave the listener behind.
  const release = () => void setTimeout(done, 0);
  document.addEventListener("click", stop, true);
  document.addEventListener("pointerup", release, true);
}

/**
 * For `Popover`: calls `onOutside` when a press lands outside the element `inside` returns, and consumes the click that finishes
 * that press, so closing a panel never also activates a control beneath it. Presses while `active` is false are ignored.
 */
export function useOutsidePress(
  inside: () => Element | null | undefined,
  onOutside: () => void,
  active = true,
) {
  const latest = useRef({ inside, onOutside });
  useEffect(() => {
    latest.current = { inside, onOutside };
  });
  useEffect(() => {
    if (!active) return;
    const press = (event: PointerEvent) => {
      if (latest.current.inside()?.contains(event.target as Node)) return;
      swallowNextClick();
      latest.current.onOutside();
    };
    document.addEventListener("pointerdown", press);
    return () => document.removeEventListener("pointerdown", press);
  }, [active]);
}
