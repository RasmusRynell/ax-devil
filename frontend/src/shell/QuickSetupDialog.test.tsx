import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { QuickSetupDialog } from "./QuickSetupDialog";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(cleanup);

const firstRun = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "ui.quick_setup_done": false },
};

describe("quick setup dialog", () => {
  it("stores a pick at once, marked as done, and says why a refused one was refused", async () => {
    const save = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce({
      reason: "no such theme",
      location: "ui.theme",
    });
    render(
      <FakeSettings state={firstRun} save={save}>
        <QuickSetupDialog onClose={() => {}} />
      </FakeSettings>,
    );
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
    expect(save).toHaveBeenCalledWith({
      values: { ...firstRun.values, "ui.theme": "dark", "ui.quick_setup_done": true },
      shortcuts: firstRun.shortcuts,
    });
    await waitFor(() => expect(screen.getByLabelText("Theme")).toHaveProperty("disabled", false));
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
    expect((await screen.findByRole("alert")).textContent).toContain("no such theme");
  });

  it("takes one write at a time: a change or a close while one is pending is not sent", async () => {
    let finish: (refused: null) => void = () => {};
    const save = vi.fn(() => new Promise<null>((resolve) => (finish = resolve)));
    const onClose = vi.fn();
    render(
      <FakeSettings state={firstRun} save={save}>
        <QuickSetupDialog onClose={onClose} />
      </FakeSettings>,
    );
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(screen.getByRole("button", { name: "Done" }).hasAttribute("disabled")).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => finish(null));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(save).toHaveBeenCalledTimes(2);
    await act(async () => finish(null));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("records itself as done when closed, with Done or Escape, and then stays closed without a write", async () => {
    const save = vi.fn(async () => null);
    const onClose = vi.fn();
    const closing = (state: typeof firstRun) => {
      cleanup();
      render(
        <FakeSettings state={state} save={save}>
          <QuickSetupDialog onClose={onClose} />
        </FakeSettings>,
      );
    };
    closing(firstRun);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      values: { ...firstRun.values, "ui.quick_setup_done": true },
      shortcuts: firstRun.shortcuts,
    });

    save.mockClear();
    closing(DEFAULT_SETTINGS as typeof firstRun);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
    expect(save).not.toHaveBeenCalled();
  });
});
