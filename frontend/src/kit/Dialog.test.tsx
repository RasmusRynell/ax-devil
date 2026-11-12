import { StrictMode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { DialogFrame } from "./Dialog";

// jsdom has no modal dialog implementation. Model its modal state and queued close event here;
// desktop/smoke-native-input.cjs verifies the browser's actual modal focus behavior.
let modal: WeakSet<HTMLDialogElement>;
beforeEach(() => {
  vi.useFakeTimers();
  modal = new WeakSet();
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.open = true;
    modal.add(this);
    this.querySelector<HTMLElement>("input, button")?.focus();
  });
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    if (!this.open) return;
    this.open = false;
    modal.delete(this);
    setTimeout(() => this.dispatchEvent(new Event("close")), 0);
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it.each(["focusSubmit changes", "StrictMode replay"])(
  "keeps the modal open without notifying onClose during %s",
  async (trigger) => {
    const opener = render(<button>Open</button>).getByRole("button");
    opener.focus();
    const onClose = vi.fn();
    const frame = (focusSubmit: boolean) => (
      <DialogFrame
        title="Question"
        focusSubmit={focusSubmit}
        onClose={onClose}
        onCancel={(event) => event.preventDefault()}
        footer={<button type="submit">OK</button>}
      >
        <input aria-label="Answer" />
      </DialogFrame>
    );
    const view = render(
      trigger === "StrictMode replay" ? <StrictMode>{frame(false)}</StrictMode> : frame(false),
    );
    if (trigger === "focusSubmit changes") view.rerender(frame(true));
    await act(() => vi.runAllTimersAsync());
    const dialog = screen.getByRole("dialog") as HTMLDialogElement;
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog.open).toBe(true);
    expect(modal.has(dialog)).toBe(true);
    expect(dialog.contains(document.activeElement)).toBe(true);
    view.unmount();
    await act(() => vi.runAllTimersAsync());
    expect(onClose).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(opener);
    // A real close must still reach the caller; suppressing all close events is not a fix.
    render(frame(false));
    act(() => (screen.getByRole("dialog") as HTMLDialogElement).close());
    await act(() => vi.runAllTimersAsync());
    expect(onClose).toHaveBeenCalledExactlyOnceWith(expect.anything());
  },
);
