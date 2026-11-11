import { useState } from "react";
import { afterEach, beforeAll, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

// Fresh modules model a page reload: Device values must come from storage, not the surviving preview store.
async function mount() {
  vi.resetModules();
  const { SettingsProvider, useSettings } = await import("../settings");
  const { AccessContext } = await import("../access");
  const { QuickSetupDialog } = await import("./QuickSetupDialog");
  const { SettingsDialog } = await import("./SettingsDialog");
  function Host() {
    const { values } = useSettings();
    const [dialog, setDialog] = useState("");
    const [notice, setNotice] = useState("");
    return (
      <>
        <output>{`${values["ui.theme"] as string}/${values["ui.text_size"] as string}/${values["ui.quick_setup_done"] as boolean}`}</output>
        <button onClick={() => setDialog("quick")}>Quick Setup</button>
        <button onClick={() => setDialog("settings")}>Settings</button>
        {notice && <button onClick={() => setNotice("")}>{notice}</button>}
        {dialog === "quick" && (
          <QuickSetupDialog onClose={() => setDialog("")} onNotice={setNotice} />
        )}
        {dialog === "settings" && (
          <SettingsDialog onClose={() => setDialog("")} onRestartNeeded={() => {}} />
        )}
      </>
    );
  }
  return render(
    <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
      <SettingsProvider onInfo={() => {}}>
        <Host />
      </SettingsProvider>
    </AccessContext.Provider>,
  );
}

it.each(["Done", "Escape", "Settings OK"])(
  "persists retained previews after refusal, unmount and %s",
  async (confirm) => {
    let refused = true;
    let remote = { revision: 0, values: { "ui.quick_setup_done": false }, shortcuts: {} };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url !== "/api/settings")
          return { ok: false, json: async () => ({ reason: "unavailable" }) };
        if (init?.method === "PATCH") {
          if (refused)
            return { ok: false, json: async () => ({ reason: "disk full", location: "" }) };
          const patch = JSON.parse(init.body as string) as { set: Record<string, unknown> };
          remote = {
            ...remote,
            revision: remote.revision + 1,
            values: { ...remote.values, ...patch.set },
          };
        }
        return { ok: true, json: async () => remote };
      }),
    );
    await mount();
    fireEvent.click(await screen.findByRole("button", { name: "Quick Setup" }));
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    fireEvent.click(screen.getByRole("radio", { name: "Larger" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Could not save these choices: disk full" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("dark/larger/false")).toBeTruthy();
    expect(window.localStorage.getItem("ax-devil.device")).toBeNull();

    // Settings stages edits; Cancel leaves the retained runtime preview and stored values alone.
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), {
      target: { value: "light" },
    });
    expect(screen.getByText("dark/larger/false")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
    expect(screen.getByText("dark/larger/false")).toBeTruthy();

    refused = false;
    if (confirm === "Settings OK") {
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(screen.getByRole("button", { name: "OK" }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "Quick Setup" }));
      expect(screen.getByRole("radio", { name: "Dark" })).toHaveProperty("checked", true);
      if (confirm === "Escape")
        fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
      else fireEvent.click(screen.getByRole("button", { name: "Done" }));
    }
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    cleanup();
    await mount();
    await screen.findByText(`dark/larger/${confirm !== "Settings OK"}`);
  },
);

it.each(["submission order", "reverse order", "first refused"])(
  "keeps the newer mixed save after Cancel and reopen: %s",
  async (order) => {
    const release: (() => void)[] = [];
    const sent: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url !== "/api/settings") return { ok: false };
        const initial = { revision: 0, values: { "ui.quick_setup_done": true }, shortcuts: {} };
        if (init?.method !== "PATCH") return { ok: true, json: async () => initial };
        const index = sent.length;
        sent.push(JSON.parse(init.body as string));
        return new Promise((resolve) => {
          release.push(() =>
            resolve(
              index === 0 && order === "first refused"
                ? { ok: false, json: async () => ({ reason: "disk full", location: "" }) }
                : {
                    ok: true,
                    json: async () => ({
                      ...initial,
                      revision: index + 1,
                      values: {
                        ...initial.values,
                        "camera.host": index === 0 ? "first.example" : "second.example",
                      },
                    }),
                  },
            ),
          );
        });
      }),
    );
    await mount();
    for (const [theme, host] of [
      ["light", "first.example"],
      ["dark", "second.example"],
    ]) {
      fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), {
        target: { value: theme },
      });
      fireEvent.click(screen.getByRole("tab", { name: "Stream defaults" }));
      fireEvent.change(screen.getByRole("textbox", { name: "Host" }), { target: { value: host } });
      fireEvent.click(screen.getByRole("button", { name: "OK" }));
      await waitFor(() => expect(release).toHaveLength(theme === "light" ? 1 : 2));
      expect(screen.getByRole("button", { name: "OK" })).toHaveProperty("disabled", true);
      if (theme === "light") {
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveProperty("disabled", false);
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(screen.queryByRole("dialog")).toBeNull();
      }
    }
    expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
    expect(sent).toEqual([
      { set: { "camera.host": "first.example" } },
      { set: { "camera.host": "second.example" } },
    ]);
    if (order === "reverse order") {
      await act(async () => release[1]());
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByText("dark/system/true")).toBeTruthy();
      const saved = window.localStorage.getItem("ax-devil.device");
      await act(async () => release[0]());
      expect(window.localStorage.getItem("ax-devil.device")).toBe(saved);
    } else {
      await act(async () => release[0]());
      expect(screen.getByRole("dialog")).toBeTruthy();
      if (order === "first refused")
        expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
      else expect(screen.getByText("light/system/true")).toBeTruthy();
      await act(async () => release[1]());
    }
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("dark/system/true")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem("ax-devil.device") ?? "{}")).toEqual({
      "ui.theme": "dark",
    });
  },
);
