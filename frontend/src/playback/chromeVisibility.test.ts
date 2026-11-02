// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { ChromeVisibility } from "./chromeVisibility";

/** The controls' visibility on a virtual clock. */
function setup() {
  let now = 0;
  let pending: { at: number; callback: () => void }[] = [];
  const state = { shown: true };
  const visibility = new ChromeVisibility(
    (shown) => (state.shown = shown),
    (callback, ms) => {
      const timer = { at: now + ms, callback };
      pending.push(timer);
      return () => (pending = pending.filter((other) => other !== timer));
    },
  );
  const advance = (ms: number) => {
    now += ms;
    for (const due of pending.filter((timer) => timer.at <= now)) due.callback();
    pending = pending.filter((timer) => timer.at > now);
  };
  return { visibility, state, advance };
}

describe("controls over the video (VPC-004)", () => {
  it("hide 2 s after the pointer last moved over the video", () => {
    const { visibility, state, advance } = setup();
    visibility.pointer(false);
    advance(1500);
    visibility.pointer(false);
    advance(1500);
    expect(state.shown).toBe(true);
    advance(500);
    expect(state.shown).toBe(false);
    visibility.pointer(false);
    expect(state.shown).toBe(true);
  });

  it("stay while the pointer is on the controls, and leaving hides at once only from the video", () => {
    const { visibility, state, advance } = setup();
    visibility.pointer(false);
    visibility.pointer(true);
    advance(10_000);
    expect(state.shown).toBe(true);
    visibility.leave();
    advance(1999);
    expect(state.shown).toBe(true);
    advance(1);
    expect(state.shown).toBe(false);

    visibility.enter();
    expect(state.shown).toBe(true);
    advance(10_000);
    expect(state.shown).toBe(true);
    visibility.pointer(false);
    visibility.leave();
    expect(state.shown).toBe(false);
  });
});
