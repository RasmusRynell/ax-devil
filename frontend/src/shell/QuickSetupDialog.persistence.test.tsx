import { browserBacking } from "../client/deviceSettings";
import { useState } from "react";
import { afterEach, beforeAll, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
  const { SettingsProvider, useSettings } = await import("../client/settings");
  const { ChangesProvider } = await import("../client/resource");
  const { AccessContext } = await import("../client/access");
  const { QuickSetupDialog } = await import("./QuickSetupDialog");
  const { ChoiceDialog } = await import("../kit/ChoiceDialog");
  const { SettingsDialog } = await import("./SettingsDialog");
  function Host() {
    const { values, apply } = useSettings();
    const [dialog, setDialog] = useState("");
    const [notice, setNotice] = useState("");
    return (
      <>
        <output>{`${values["ui.theme"] as string}/${values["ui.text_size"] as string}/${values["ui.quick_setup_done"] as boolean}`}</output>
        <output>{values["camera.protocol"] as string}</output>
        <button
          onClick={() => void apply({ reset: ["ui.theme", "ui.text_size", "camera.protocol"] })}
        >
          Reset choices
        </button>
        <button onClick={() => setDialog("quick")}>Quick Setup</button>
        <button onClick={() => setDialog("settings")}>Settings</button>
        {notice && (
          <ChoiceDialog
            title="Quick Setup"
            message={notice}
            choices={[{ id: "ok", label: "OK" }]}
            defaultId="ok"
            onChoose={() => setNotice("")}
          />
        )}
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
    <ChangesProvider>
      <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
        <SettingsProvider>
          <Host />
        </SettingsProvider>
      </AccessContext.Provider>
    </ChangesProvider>,
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
          return {
            ok: false,
            json: async () => ({ code: "Internal", params: {}, reason: "unavailable" }),
          };
        if (init?.method === "PATCH") {
          if (refused)
            return {
              ok: false,
              json: async () => ({ code: "Internal", params: {}, reason: "disk full" }),
            };
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
    await dismissRefusal();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("dark/larger/false")).toBeTruthy();
    expect(browserBacking().read()).toEqual({});

    // Settings stages edits; Cancel leaves the retained runtime preview and stored values alone.
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), {
      target: { value: "light" },
    });
    expect(screen.getByText("dark/larger/false")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(browserBacking().read()).toEqual({});
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
                ? {
                    ok: false,
                    json: async () => ({ code: "Internal", params: {}, reason: "disk full" }),
                  }
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
    expect(browserBacking().read()).toEqual({});
    expect(sent).toEqual([
      { set: { "camera.host": "first.example" } },
      { set: { "camera.host": "second.example" } },
    ]);
    if (order === "reverse order") {
      await act(async () => release[1]());
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByText("dark/system/true")).toBeTruthy();
      const saved = browserBacking().read();
      await act(async () => release[0]());
      expect(browserBacking().read()).toEqual(saved);
    } else {
      await act(async () => release[0]());
      expect(screen.getByRole("dialog")).toBeTruthy();
      if (order === "first refused") expect(browserBacking().read()).toEqual({});
      else expect(screen.getByText("light/system/true")).toBeTruthy();
      await act(async () => release[1]());
    }
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("dark/system/true")).toBeTruthy();
    expect(browserBacking().read()).toEqual({
      "ui.theme": "dark",
    });
  },
);

async function dismissRefusal() {
  await screen.findByText("Could not save these choices: disk full");
  await screen.findByRole("dialog");
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "OK" }));
  expect(screen.queryByRole("dialog")).toBeNull();
}

it.each([
  ["Settings", "auto"],
  ["Settings", "light"],
  ["Settings and Server", "auto"],
  ["Quick Setup", "auto"],
  ["Quick Setup", "light"],
  ["Reset choices", "auto"],
  ["Reset choices", "light"],
])("confirms %s over a retained preview with saved theme %s", async (where, saved) => {
  if (saved === "light")
    window.localStorage.setItem("ax-devil.device", JSON.stringify({ "ui.theme": "light" }));
  const kept = window.localStorage.getItem("ax-devil.device");
  let refused = true;
  let remote = {
    revision: 0,
    values: { "ui.quick_setup_done": false, "camera.protocol": "https" },
    shortcuts: {},
  };
  const patches: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url !== "/api/settings") return { ok: false };
      if (init?.method === "PATCH") {
        const patch = JSON.parse(init.body as string) as {
          set?: Record<string, unknown>;
          reset?: string[];
        };
        patches.push(patch);
        if (refused)
          return {
            ok: false,
            json: async () => ({ code: "Internal", params: {}, reason: "disk full" }),
          };
        remote = {
          ...remote,
          revision: remote.revision + 1,
          values: { ...remote.values, ...patch.set },
        };
        if (patch.reset?.includes("camera.protocol")) remote.values["camera.protocol"] = "https";
      }
      return { ok: true, json: async () => remote };
    }),
  );
  await mount();
  fireEvent.click(await screen.findByRole("button", { name: "Quick Setup" }));
  fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
  fireEvent.click(screen.getByRole("radio", { name: "Larger" }));
  fireEvent.click(screen.getByRole("radio", { name: "HTTP" }));
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  await dismissRefusal();
  expect(screen.getByText("dark/larger/false")).toBeTruthy();
  expect(screen.getByText("https")).toBeTruthy();
  expect(browserBacking().read()).toEqual(JSON.parse(kept ?? "{}"));

  if (where.startsWith("Settings")) {
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveProperty("value", "dark");
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), { target: { value: saved } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("dark/larger/false")).toBeTruthy();
    expect(browserBacking().read()).toEqual(JSON.parse(kept ?? "{}"));
  }
  refused = false;
  fireEvent.click(
    screen.getByRole("button", { name: where.startsWith("Settings") ? "Settings" : where }),
  );
  if (where.startsWith("Settings")) {
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), { target: { value: saved } });
    fireEvent.change(screen.getByRole("combobox", { name: "Text size" }), {
      target: { value: "system" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "Stream defaults" }));
    fireEvent.click(screen.getByRole("radio", { name: "HTTP" }));
    fireEvent.click(screen.getByRole("radio", { name: "HTTPS" }));
    if (where === "Settings and Server")
      fireEvent.change(screen.getByRole("textbox", { name: "Host" }), {
        target: { value: "chosen.example" },
      });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
  } else if (where === "Quick Setup") {
    fireEvent.click(
      within(screen.getByRole("radiogroup", { name: "Theme" })).getByRole("radio", {
        name: saved === "auto" ? "System" : "Light",
      }),
    );
    fireEvent.click(
      within(screen.getByRole("radiogroup", { name: "Text size" })).getByRole("radio", {
        name: "System",
      }),
    );
    fireEvent.click(screen.getByRole("radio", { name: "HTTP" }));
    fireEvent.click(screen.getByRole("radio", { name: "HTTPS" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
  }
  const chosen = where === "Reset choices" ? "auto" : saved;
  const completed = where === "Quick Setup";
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await screen.findByText(`${chosen}/system/${completed}`);
  expect(browserBacking().read()).toEqual(
    where === "Reset choices" && saved === "light" ? {} : JSON.parse(kept ?? "{}"),
  );
  expect(patches).toHaveLength(where === "Settings" ? 1 : 2);
  cleanup();
  await mount();
  await screen.findByText(`${chosen}/system/${completed}`);
  expect(screen.getByText("https")).toBeTruthy();
});
