import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "./access";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import { SettingsProvider, useSettings } from "./settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function Catalog() {
  const { values, shortcuts, save } = useSettings();
  return (
    <button
      onClick={() =>
        void save({
          values: { ...values, "overlays.default_catalog": "user:night.json" },
          shortcuts,
        })
      }
    >
      {(values["overlays.default_catalog"] as string) || "built-in"}
    </button>
  );
}

function TextSize() {
  const { values, shortcuts, save } = useSettings();
  return (
    <button
      onClick={() => void save({ values: { ...values, "ui.text_size": "large" }, shortcuts })}
    >
      Larger text
    </button>
  );
}

describe("settings", () => {
  it("applies the theme and text size to the document root as they are saved", async () => {
    render(
      <SettingsProvider>
        <TextSize />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Larger text" }));
    await waitFor(() => expect(document.documentElement.dataset.textSize).toBe("large"));
    expect(document.documentElement.dataset.theme).toBe("auto");
  });

  it("keeps the Personal default catalog of a browser without a backend for it in the browser", async () => {
    render(
      <SettingsProvider>
        <Catalog />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "built-in" }));
    await waitFor(() => screen.getByRole("button", { name: "user:night.json" }));
    expect(JSON.parse(window.localStorage.getItem("ax-devil.preferences") ?? "{}")).toMatchObject({
      values: { "overlays.default_catalog": "user:night.json" },
    });
    cleanup();
    render(
      <SettingsProvider>
        <Catalog />
      </SettingsProvider>,
    );
    expect(screen.getByRole("button", { name: "user:night.json" })).toBeTruthy();
  });

  it("keeps the app and the settings already read when a refresh from the backend fails", async () => {
    const answers = [
      { ok: true, json: async () => DEFAULT_SETTINGS },
      { ok: false, status: 503, json: async () => null },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answers.shift()),
    );
    function Reload() {
      const { reload, values } = useSettings();
      return <button onClick={() => void reload()}>{values["ui.theme"] as string}</button>;
    }
    render(
      <GrantsContext.Provider value={["configuration"]}>
        <SettingsProvider>
          <Reload />
        </SettingsProvider>
      </GrantsContext.Provider>,
    );
    fireEvent.click(await screen.findByRole("button"));
    await waitFor(() => expect(answers).toHaveLength(0));
    expect(
      screen.getByRole("button", { name: DEFAULT_SETTINGS.values["ui.theme"] as string }),
    ).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the settings just saved when an older refresh answers after the save", async () => {
    const stale = { ...DEFAULT_SETTINGS, values: { ...DEFAULT_SETTINGS.values } };
    const saved = {
      ...DEFAULT_SETTINGS,
      values: { ...DEFAULT_SETTINGS.values, "storage.cache_dir": "/saved" },
    };
    let answerRefresh: (state: unknown) => void = () => {};
    const refresh = new Promise((resolve) => {
      answerRefresh = resolve;
    });
    const answers = [Promise.resolve(DEFAULT_SETTINGS), refresh, Promise.resolve(saved)];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => answers.shift() })),
    );
    function Folder() {
      const { reload, save, values, shortcuts } = useSettings();
      return (
        <>
          <button onClick={() => void reload()}>Reload</button>
          <button
            onClick={() =>
              void save({ values: { ...values, "storage.cache_dir": "/saved" }, shortcuts })
            }
          >
            Save
          </button>
          <p>{`folder ${values["storage.cache_dir"] as string}`}</p>
        </>
      );
    }
    render(
      <GrantsContext.Provider value={["configuration"]}>
        <SettingsProvider>
          <Folder />
        </SettingsProvider>
      </GrantsContext.Provider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Reload" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("folder /saved");
    answerRefresh(stale);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("folder /saved")).toBeTruthy();
  });
});
