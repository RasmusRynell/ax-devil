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

  /** The pointer entered the pane: the controls show, and nothing is timed until it moves. */
  enter(): void {
    this.show(true);
  }

  /** The pointer left the pane: from the video the controls hide now, from a held element they wait out the idle time. */
  leave(): void {
    this.#stop();
    if (this.#held) this.#idle();
    else this.show(false);
  }

  /** Cancels what is pending. */
  dispose(): void {
    this.#stop();
  }

  #idle(): void {
    this.#cancel = this.after(() => {
      this.#cancel = null;
      this.show(false);
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
 * element under the pane marks what the pointer is over: `data-keeps-chrome` for the controls, `data-chrome-idle` for
 * the video; the rest, such as the side panels, keeps the controls too.
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
    const enter = () => visibility.enter();
    const leave = () => visibility.leave();
    element.addEventListener("pointermove", pointer);
    element.addEventListener("pointerover", pointer);
    element.addEventListener("pointerdown", pointer);
    element.addEventListener("pointerenter", enter);
    element.addEventListener("pointerleave", leave);
    return () => {
      element.removeEventListener("pointermove", pointer);
      element.removeEventListener("pointerover", pointer);
      element.removeEventListener("pointerdown", pointer);
      element.removeEventListener("pointerenter", enter);
      element.removeEventListener("pointerleave", leave);
      visibility.dispose();
    };
  }, [pane]);
}
