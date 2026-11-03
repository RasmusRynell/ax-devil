import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { QuickSetupDialog } from "./QuickSetupDialog";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.textSize;
});

const firstRun = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "ui.quick_setup_done": false },
};

function open(
  state: typeof firstRun,
  save: (change: unknown) => Promise<null | { reason: string; location: string }>,
  { onClose = () => {}, onNotice = () => {} } = {},
) {
  render(
    <GrantsContext.Provider value={["configuration"]}>
      <FakeSettings state={state} save={save as never}>
        <QuickSetupDialog onClose={onClose} onNotice={onNotice} />
      </FakeSettings>
    </GrantsContext.Provider>,
  );
}

const pick = (group: string, option: string) =>
  fireEvent.click(
    within(screen.getByRole("radiogroup", { name: group })).getByRole("radio", { name: option }),
  );

describe("quick setup dialog", () => {
  it("previews theme and text size as they are picked and stores every choice, marked done, on Done", async () => {
    const save = vi.fn(async () => null);
    const onClose = vi.fn();
    open(firstRun, save, { onClose });
    pick("Theme", "Dark");
    pick("Text size", "Larger");
    pick("Camera protocol", "HTTP");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.textSize).toBe("larger");
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      values: {
        ...firstRun.values,
        "ui.theme": "dark",
        "ui.text_size": "larger",
        "defaults.device.protocol": "http",
        "ui.quick_setup_done": true,
      },
      shortcuts: firstRun.shortcuts,
    });
  });

  it("says why a refused write was refused and closes", async () => {
    const save = vi.fn(async () => ({ reason: "disk full", location: "" }));
    const onClose = vi.fn();
    const onNotice = vi.fn();
    open(firstRun, save, { onClose, onNotice });
    pick("Theme", "Light");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onNotice).toHaveBeenCalledWith("Could not save these choices: disk full");
  });

  it("takes one write at a time: a second Done or Escape while one is pending is not sent", async () => {
    let finish: (refused: null) => void = () => {};
    const save = vi.fn(() => new Promise<null>((resolve) => (finish = resolve)));
    const onClose = vi.fn();
    open(firstRun, save, { onClose });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(save).toHaveBeenCalledTimes(1);
    await act(async () => finish(null));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("records itself as done when closed with Escape, and then closes without a write", async () => {
    const save = vi.fn(async () => null);
    const onClose = vi.fn();
    open(firstRun, save, { onClose });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      values: { ...firstRun.values, "ui.quick_setup_done": true },
      shortcuts: firstRun.shortcuts,
    });

    cleanup();
    save.mockClear();
    open(DEFAULT_SETTINGS as typeof firstRun, save, { onClose });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
    expect(save).not.toHaveBeenCalled();
  });

  it("asks for the camera protocol only of someone who may configure the server", () => {
    render(
      <FakeSettings state={firstRun}>
        <QuickSetupDialog onClose={() => {}} onNotice={() => {}} />
      </FakeSettings>,
    );
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeTruthy();
    expect(screen.queryByRole("radiogroup", { name: "Camera protocol" })).toBeNull();
  });
});
