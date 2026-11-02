import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, createEvent, fireEvent, render, screen } from "@testing-library/react";
import { ChoiceDialog } from "./ChoiceDialog";

afterEach(cleanup);

describe("choice dialog", () => {
  it("opens on the default answer, ignores Escape, and closes only on a choice", () => {
    const choose = vi.fn();
    render(
      <ChoiceDialog
        title="Restart"
        message="Restart now?"
        choices={[
          { id: "now", label: "Restart now" },
          { id: "later", label: "Later" },
        ]}
        defaultId="later"
        onChoose={choose}
      />,
    );
    // Enter on the focused button is the browser's own click, so the default answer is whatever has focus.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Later" }));
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Restart now",
      "Later",
    ]);

    const escape = createEvent("cancel", document.querySelector("dialog") as HTMLDialogElement, {
      cancelable: true,
    });
    fireEvent(document.querySelector("dialog") as HTMLDialogElement, escape);
    expect(escape.defaultPrevented).toBe(true);
    expect(choose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));
    expect(choose).toHaveBeenCalledExactlyOnceWith("now");
  });
});
