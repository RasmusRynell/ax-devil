import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { ShortcutsDialog } from "./ShortcutsDialog";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(cleanup);

describe("shortcuts dialog", () => {
  it("refuses reserved keys, offers to take a key from its owner, and resets to the default", async () => {
    const save = vi.fn(async () => null);
    render(
      <FakeSettings save={save}>
        <ShortcutsDialog onClose={() => {}} />
      </FakeSettings>,
    );
    const record = (command: string, init: KeyboardEventInit) => {
      const button = screen.getByRole("button", { name: `Key for ${command}` });
      fireEvent.click(button);
      fireEvent.keyDown(button, init);
    };
    const status = () => screen.getByRole("status").textContent;
    const binding = (command: string) =>
      document.getElementById(
        screen
          .getByRole("button", { name: `Key for ${command}` })
          .getAttribute("aria-describedby") ?? "",
      )?.textContent;
    expect(binding("Exit")).toContain("Q");
    fireEvent.click(screen.getByRole("button", { name: "Key for Exit" }));
    expect(binding("Exit")).toBe("Press a key…");
    expect(status()).toBe("Press a key for Exit");
    record("Exit", { key: "-", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toContain("reserved");

    record("Exit", { key: "s", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toContain("already used by Save Workspace");
    fireEvent.click(screen.getByRole("button", { name: "Reassign" }));
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await act(async () => {});
    expect(status()).toBe("Exit set to Ctrl+S, taken from Save Workspace");
    expect(binding("Exit")).toContain("S");
    expect(save).toHaveBeenLastCalledWith({
      values: DEFAULT_SETTINGS.values,
      shortcuts: { ...DEFAULT_SETTINGS.shortcuts, exit: "Ctrl+S", "save-workspace": null },
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset Exit" }));
    expect(status()).toMatch(/^Exit reset to /);
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(save).toHaveBeenLastCalledWith({
      values: DEFAULT_SETTINGS.values,
      shortcuts: { ...DEFAULT_SETTINGS.shortcuts, "save-workspace": null },
    });
  });
});
