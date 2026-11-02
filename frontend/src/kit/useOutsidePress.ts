import { useEffect, useRef } from "react";

/** Swallows the click that completes the press that just happened, so what is under the pointer is not activated. */
function swallowNextClick() {
  const done = () => {
    document.removeEventListener("click", stop, true);
    document.removeEventListener("pointerup", release, true);
    document.removeEventListener("pointercancel", release, true);
  };
  const stop = (event: Event) => {
    event.stopPropagation();
    event.preventDefault();
    done();
  };
  // A press that ends without a click (a drag, a scroll bar, a cancelled touch) must not leave the listener behind:
  // after pointerup the click follows at once, so one still pending a task later is not coming.
  const release = () => void setTimeout(done, 0);
  document.addEventListener("click", stop, true);
  document.addEventListener("pointerup", release, true);
  document.addEventListener("pointercancel", release, true);
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
