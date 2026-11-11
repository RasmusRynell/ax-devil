import { afterEach, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render } from "@testing-library/react";
import type { WindowBridge } from "../windowBridge";
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
    let held = false;
    header.setPointerCapture = () => {
      held = true;
    };
    header.hasPointerCapture = () => held;
    const release = vi.fn(() => {
      held = false;
    });
    header.releasePointerCapture = release;
    restoreDrag.mockClear();
    fireEvent.pointerDown(view.getByText("Title"), { button: 0, buttons: 1 });
    expect(held).toBe(true);
    if (exit === "Escape") fireEvent.keyDown(window, { key: "Escape" });
    else if (exit === "blur") fireEvent.blur(window);
    else if (exit === "unmount") view.unmount();
    else if (exit === "pointerCancel") fireEvent.pointerCancel(header);
    else if (exit === "pointerUp") fireEvent.pointerUp(header);
    else fireEvent.lostPointerCapture(header);
    expect(release).toHaveBeenCalledOnce();
    expect(restoreDrag.mock.calls.map(([phase]) => phase)).toEqual(["start", "end"]);
    fireEvent.pointerMove(header, { buttons: 1 });
    expect(restoreDrag).toHaveBeenCalledTimes(2);
    view.unmount();
  }
});
