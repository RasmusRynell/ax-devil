import { afterEach, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render } from "@testing-library/react";
import type { WindowBridge } from "../client/windowBridge";
import { useTitleDrag } from "./useTitleDrag";

function Title() {
  const { manual, ...events } = useTitleDrag();
  return (
    <header {...events} data-manual={manual}>
      <span data-cursor-id="title:drag">Title</span>
    </header>
  );
}
afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("every gesture exit clears main state and releases capture before later movement", () => {
  class Pointer extends MouseEvent {
    readonly pointerId = 1;
  }
  vi.stubGlobal("PointerEvent", Pointer);
  const restoreDrag = vi.fn();
  window.axDevilWindow = {
    restoreDrag,
    onMaximizedChange: (listener: (value: boolean) => void) => {
      listener(true);
      return () => {};
    },
  } as unknown as WindowBridge;
  for (const exit of [
    "Escape",
    "blur",
    "pointerCancel",
    "lostPointerCapture",
    "pointerUp",
    "unmount",
  ]) {
    const view = render(<Title />);
    const header = view.container.querySelector("header")!;
    const title = view.getByText("Title");
    let held = false;
    title.setPointerCapture = () => {
      held = true;
    };
    title.hasPointerCapture = () => held;
    const release = vi.fn(() => {
      held = false;
    });
    title.releasePointerCapture = release;
    restoreDrag.mockClear();
    fireEvent.pointerDown(title, { button: 0, buttons: 1 });
    expect(held).toBe(true);
    if (exit === "Escape") fireEvent.keyDown(window, { key: "Escape" });
    else if (exit === "blur") fireEvent.blur(window);
    else if (exit === "unmount") view.unmount();
    else if (exit === "pointerCancel") fireEvent.pointerCancel(title);
    else if (exit === "pointerUp") fireEvent.pointerUp(title);
    else fireEvent.lostPointerCapture(title);
    expect(release).toHaveBeenCalledOnce();
    expect(restoreDrag.mock.calls.map(([phase]) => phase)).toEqual(["start", "end"]);
    fireEvent.pointerMove(header, { buttons: 1 });
    expect(restoreDrag).toHaveBeenCalledTimes(2);
    view.unmount();
  }
});
