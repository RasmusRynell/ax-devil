import { useEffect, type RefObject } from "react";

/** How long the pointer may rest over the video before the controls fade. */
const IDLE_MS = 2000;

/**
 * When the controls over the video are shown. The pointer shows them; resting on the video hides them after a while,
 * and resting on the controls or the panels beside the video keeps them. Time is injected: `after` runs a callback
 * later and returns how to cancel it.
 */
export class ChromeVisibility {
  #held = false;
  #cancel: (() => void) | null = null;

  constructor(
    private readonly show: (shown: boolean) => void,
    private readonly after: (callback: () => void, ms: number) => () => void,
  ) {}

  /** The pointer moved, pressed or crossed into an element: `held` when that element keeps the controls shown. */
  pointer(held: boolean): void {
    this.#held = held;
    this.show(true);
    this.#stop();
    if (!held) this.#idle();
  }

  /**
   * The pointer came into the pane onto an element (`held` as for `pointer`): the controls show and no new time is
   * armed, but a wait already pending runs on, and hides them unless the pointer is on a held element by then.
   */
  enter(held: boolean): void {
    this.#held = held;
    this.show(true);
  }

  /**
   * The pointer left the pane: from the video the controls hide now and a pending wait is left to run; from a held
   * element the wait starts over.
   */
  leave(): void {
    const held = this.#held;
    this.#held = false;
    if (!held) {
      this.show(false);
      return;
    }
    this.#stop();
    this.#idle();
  }

  /** Cancels what is pending. */
  dispose(): void {
    this.#stop();
  }

  #idle(): void {
    this.#cancel = this.after(() => {
      this.#cancel = null;
      if (!this.#held) this.show(false);
    }, IDLE_MS);
  }

  #stop(): void {
    this.#cancel?.();
    this.#cancel = null;
  }
}

/** Whether an event came from somewhere that keeps the controls: the controls or a panel, not the bare video. */
function keepsChrome(event: Event): boolean {
  const marker =
    event.target instanceof Element
      ? event.target.closest("[data-keeps-chrome], [data-chrome-idle]")
      : null;
  return !marker?.hasAttribute("data-chrome-idle");
}

/**
 * Shows and hides the controls over the video as the pointer moves over `pane`, by setting its `data-chrome` to
 * `shown` or `hidden` for the stylesheet to fade. Nothing is held in React state, so a pointer move costs no render. An
 * element under the pane marks what the pointer is over: `data-keeps-chrome` for the controls and the warnings on the
 * video, `data-chrome-idle` for the rest of the video; the rest, such as the side panels, keeps the controls too.
 * A pointer coming in from outside the pane shows them and arms no time; the first move after it starts the idle time.
 */
export function useChromeVisibility(pane: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const element = pane.current;
    if (!element) return;
    const visibility = new ChromeVisibility(
      (shown) => {
        const value = shown ? "shown" : "hidden";
        if (element.dataset.chrome !== value) element.dataset.chrome = value;
      },
      (callback, ms) => {
        const timer = setTimeout(callback, ms);
        return () => clearTimeout(timer);
      },
    );
    const pointer = (event: Event) => visibility.pointer(keepsChrome(event));
    // `pointerover` comes before `pointerenter` and names the element entered, which `pointerenter` on the pane does not.
    const over = (event: PointerEvent) => {
      const from = event.relatedTarget;
      if (from instanceof Node && element.contains(from)) pointer(event);
      else visibility.enter(keepsChrome(event));
    };
    const leave = () => visibility.leave();
    element.addEventListener("pointermove", pointer);
    element.addEventListener("pointerover", over);
    element.addEventListener("pointerdown", pointer);
    element.addEventListener("pointerleave", leave);
    return () => {
      element.removeEventListener("pointermove", pointer);
      element.removeEventListener("pointerover", over);
      element.removeEventListener("pointerdown", pointer);
      element.removeEventListener("pointerleave", leave);
      visibility.dispose();
    };
  }, [pane]);
}
