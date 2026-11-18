import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { ChromeVisibility, useChromeVisibility } from "./chromeVisibility";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

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
  it("a wait pending when the pointer leaves the video still ends a later visit", () => {
    const { visibility, state, advance } = setup();
    visibility.pointer(false);
    advance(500);
    visibility.leave();
    expect(state.shown).toBe(false);
    advance(1000);
    visibility.enter(false);
    expect(state.shown).toBe(true);
    advance(500);
    expect(state.shown).toBe(false);
  });

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

    visibility.enter(false);
    expect(state.shown).toBe(true);
    advance(10_000);
    expect(state.shown).toBe(true);
    visibility.pointer(false);
    visibility.leave();
    expect(state.shown).toBe(false);
  });
});

function Pane() {
  const pane = useRef<HTMLDivElement>(null);
  useChromeVisibility(pane);
  return (
    <div ref={pane} data-testid="pane">
      <div data-chrome-idle="" data-testid="video" />
      <div data-keeps-chrome="" data-testid="controls" />
    </div>
  );
}

describe("controls over the video, from the DOM events (VPC-004)", () => {
  it("a pointer entering shows them untimed, and the first move after it starts the idle time", () => {
    vi.useFakeTimers();
    render(<Pane />);
    const pane = screen.getByTestId("pane");
    const video = screen.getByTestId("video");
    // The pointer comes onto the video from outside the pane.
    fireEvent.pointerOver(video, { relatedTarget: document.body });
    vi.advanceTimersByTime(10_000);
    expect(pane.dataset.chrome).toBe("shown");

    fireEvent.pointerMove(video);
    vi.advanceTimersByTime(2000);
    expect(pane.dataset.chrome).toBe("hidden");
  });

  it("coming back over the video keeps the deadline left by leaving from the controls", () => {
    vi.useFakeTimers();
    const { unmount } = render(<Pane />);
    const pane = screen.getByTestId("pane");
    const video = screen.getByTestId("video");
    fireEvent.pointerOver(screen.getByTestId("controls"), { relatedTarget: document.body });
    fireEvent.pointerLeave(pane);
    vi.advanceTimersByTime(1500);
    fireEvent.pointerOver(video, { relatedTarget: document.body });
    expect(pane.dataset.chrome).toBe("shown");
    vi.advanceTimersByTime(500);
    expect(pane.dataset.chrome).toBe("hidden");

    // Entry arms no time of its own.
    fireEvent.pointerOver(video, { relatedTarget: document.body });
    vi.advanceTimersByTime(10_000);
    expect(pane.dataset.chrome).toBe("shown");

    // The deadline passing over a control that keeps them hides nothing; crossing onto the video times them again.
    const controls = screen.getByTestId("controls");
    fireEvent.pointerOver(controls, { relatedTarget: document.body });
    fireEvent.pointerLeave(pane);
    vi.advanceTimersByTime(1500);
    fireEvent.pointerOver(controls, { relatedTarget: document.body });
    vi.advanceTimersByTime(1000);
    expect(pane.dataset.chrome).toBe("shown");
    fireEvent.pointerOver(video, { relatedTarget: controls });
    vi.advanceTimersByTime(2000);
    expect(pane.dataset.chrome).toBe("hidden");

    // Unmounting mid-wait cancels it and detaches the listeners.
    fireEvent.pointerMove(video);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    pane.dataset.chrome = "hidden";
    fireEvent.pointerMove(video);
    expect(vi.getTimerCount()).toBe(0);
    expect(pane.dataset.chrome).toBe("hidden");
  });
});
