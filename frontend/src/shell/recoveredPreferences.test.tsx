import { afterEach, beforeAll, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AccessContext } from "../client/access";
import { browserBacking, deviceStore } from "../client/deviceSettings";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import { SettingsProvider } from "../client/settings";
import { QuickSetupDialog } from "./QuickSetupDialog";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.dispatchEvent(new StorageEvent("storage", { key: null }));
});

it.each(["Settings", "Quick Setup"])(
  "%s repairs an explicitly edited Device fallback, keeps an untouched neighbor, and skips a valid round trip",
  async (dialog) => {
    const raw = { "ui.theme": { damaged: true }, "ui.text_size": "invalid" };
    window.localStorage.setItem("ax-devil.device", JSON.stringify(raw));
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    vi.stubGlobal("fetch", async () => ({ ok: true, json: async () => DEFAULT_SETTINGS }));
    const close = vi.fn();
    const stored = () => JSON.stringify(browserBacking().read());
    for (const recovered of [true, false]) {
      const before = stored();
      render(
        <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
          <SettingsProvider>
            {dialog === "Settings" ? (
              <SettingsDialog onClose={close} onRestartNeeded={() => {}} />
            ) : (
              <QuickSetupDialog onClose={close} onNotice={() => {}} />
            )}
          </SettingsProvider>
        </AccessContext.Provider>,
      );
      if (dialog === "Settings") {
        const theme = await screen.findByRole("combobox", { name: "Theme" });
        for (const value of ["dark", "auto"]) fireEvent.change(theme, { target: { value } });
      } else {
        const theme = await screen.findByRole("radiogroup", { name: "Theme" });
        for (const name of ["Dark", "System"])
          fireEvent.click(within(theme).getByRole("radio", { name }));
      }
      fireEvent.click(screen.getByRole("button", { name: dialog === "Settings" ? "OK" : "Done" }));
      await waitFor(() => expect(close).toHaveBeenCalled());
      expect(JSON.parse(stored()!)).toEqual({ ...raw, "ui.theme": "auto" });
      if (!recovered) expect(stored()).toBe(before);
      cleanup();
      close.mockClear();
      // Reload the persisted document through the store's real external-change path.
      await act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
      expect(deviceStore.readRecovered()).toEqual(["ui.text_size"]);
    }
  },
);

it("an explicit shortcut reset repairs a recovered default without sending an untouched recovered neighbor", async () => {
  const apply = vi.fn(async () => null);
  render(
    <FakeSettings
      state={{ ...DEFAULT_SETTINGS, recoveredShortcuts: ["exit", "open-workspace"] }}
      apply={apply}
    >
      <ShortcutsDialog onClose={() => {}} />
    </FakeSettings>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Reset Exit" }));
  fireEvent.click(screen.getByRole("button", { name: "OK" }));
  await waitFor(() => expect(apply).toHaveBeenCalledWith({ shortcutsReset: ["exit"] }));
});
