import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import type { ErrorWire } from "../generated/api";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { textOf } from "./SettingField";

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
  save: (change: unknown) => Promise<ErrorWire | null>,
  { onClose = () => {}, onNotice = () => {} } = {},
) {
  render(
    <FakeSettings state={state} apply={save as never}>
      <QuickSetupDialog onClose={onClose} onNotice={onNotice} />
    </FakeSettings>,
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
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.textSize).toBe("larger");
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      set: { "ui.theme": "dark", "ui.text_size": "larger", "ui.quick_setup_done": true },
    });
  });

  it("offers the camera protocol and shows a stored choice when reopened", async () => {
    const save = vi.fn(async () => null);
    const onClose = vi.fn();
    open(firstRun, save, { onClose });
    pick("Camera protocol", "HTTP");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    const { set } = (save.mock.calls[0] as unknown as [{ set: Record<string, unknown> }])[0];
    expect(set["camera.protocol"]).toBe("http");

    cleanup();
    open({ ...firstRun, values: { ...firstRun.values, ...set } } as typeof firstRun, save);
    expect(screen.getByRole("radio", { name: "HTTP" })).toHaveProperty("checked", true);
  });

  it("says why a refused write was refused, closes, and takes the preview back", async () => {
    const save = vi.fn(async (): Promise<ErrorWire> => ({
      code: "Internal",
      params: {},
      reason: "disk full",
    }));
    const onClose = vi.fn();
    const onNotice = vi.fn();
    // What the provider shows for the stored settings.
    document.documentElement.dataset.theme = textOf(firstRun.values["ui.theme"]);
    document.documentElement.dataset.textSize = textOf(firstRun.values["ui.text_size"]);
    open(firstRun, save, { onClose, onNotice });
    pick("Theme", "Light");
    pick("Text size", "Larger");
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onNotice).toHaveBeenCalledWith("Could not save these choices: disk full");
    expect(document.documentElement.dataset.theme).toBe(firstRun.values["ui.theme"]);
    expect(document.documentElement.dataset.textSize).toBe(firstRun.values["ui.text_size"]);
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
    expect(save).toHaveBeenCalledWith({ set: { "ui.quick_setup_done": true } });

    cleanup();
    save.mockClear();
    open(DEFAULT_SETTINGS as typeof firstRun, save, { onClose });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
    expect(save).not.toHaveBeenCalled();
  });
});
