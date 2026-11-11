import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "./access";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import { ChangesProvider, useNoticeSink } from "./resource";
import { deviceStore } from "./deviceSettings";
import { SettingsProvider, useSettings } from "./settings";

afterEach(() => {
  cleanup();
  deviceStore.reset("ui.theme");
  deviceStore.reset("ui.text_size");
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function Catalog() {
  const { values, set } = useSettings();
  const [refused, setRefused] = useState(false);
  return (
    <>
      <button
        onClick={() =>
          void set("overlays.default_catalog", "user:night.json").then(
            (refusal) => refusal && setRefused(true),
          )
        }
      >
        {(values["overlays.default_catalog"] as string) || "built-in"}
      </button>
      {refused && <p>refused</p>}
    </>
  );
}

function TextSize() {
  const { set } = useSettings();
  return <button onClick={() => void set("ui.text_size", "large")}>Larger text</button>;
}

describe("settings", () => {
  it("stores a Device setting on the device, applies it and sends no request, even for a Guest", async () => {
    const fetched = vi.fn();
    vi.stubGlobal("fetch", fetched);
    render(
      <SettingsProvider>
        <TextSize />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Larger text" }));
    await waitFor(() => expect(document.documentElement.dataset.textSize).toBe("large"));
    expect(JSON.parse(window.localStorage.getItem("ax-devil.device") ?? "{}")).toEqual({
      "ui.text_size": "large",
    });
    expect(fetched).not.toHaveBeenCalled();
    deviceStore.reset("ui.text_size");
  });

  it("gives a Guest the default of every Personal setting, refuses its change and drops the old copy", async () => {
    window.localStorage.setItem(
      "ax-devil.preferences",
      JSON.stringify({ values: { "overlays.default_catalog": "user:night.json" } }),
    );
    render(
      <SettingsProvider>
        <Catalog />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "built-in" }));
    await screen.findByText("refused");
    expect(window.localStorage.getItem("ax-devil.preferences")).toBeNull();
  });
});

function Choices() {
  const { values, apply, previewDevice } = useSettings();
  const noticed = useNoticeSink();
  const [result, setResult] = useState("");
  return (
    <>
      <button
        onClick={() =>
          void apply({ set: { "camera.protocol": "http", "ui.theme": "light" } }).then(() =>
            setResult("earlier finished"),
          )
        }
      >
        Earlier save
      </button>
      <button
        onClick={() => {
          previewDevice("ui.theme", "dark");
          previewDevice("ui.text_size", "larger");
        }}
      >
        Preview
      </button>
      <button onClick={() => noticed?.("settings", 5, false)}>Other Owner</button>
      <button
        onClick={() =>
          void apply({
            set: { "ui.theme": "dark", "ui.text_size": "larger", "ui.quick_setup_done": true },
          }).then((refusal) => setResult(refusal ? "refused" : "saved"))
        }
      >
        Finish setup
      </button>
      <p>{`${values["ui.theme"] as string}/${values["ui.text_size"] as string}`}</p>
      <p>{values["overlays.default_catalog"] as string}</p>
      <p>{values["ui.quick_setup_done"] ? "complete" : "pending"}</p>
      <p>{result}</p>
    </>
  );
}

it("keeps newer Device previews through a refused patch, Owner push and older mixed save", async () => {
  let finish: (state: unknown) => void = () => {};
  const earlier = new Promise((resolve) => {
    finish = resolve;
  });
  const { "ui.theme": _theme, "ui.text_size": _size, ...values } = DEFAULT_SETTINGS.values;
  const initial = { ...DEFAULT_SETTINGS, values: { ...values, "ui.quick_setup_done": false } };
  let reads = 0;
  const patches: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/info") return { ok: false };
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(init.body as string));
        return patches.length === 1
          ? { ok: true, json: () => earlier }
          : {
              ok: false,
              json: async () => ({ reason: "disk full", location: "ui.quick_setup_done" }),
            };
      }
      reads += 1;
      return {
        ok: true,
        json: async () =>
          reads === 1
            ? initial
            : {
                ...initial,
                revision: 5,
                values: { ...initial.values, "overlays.default_catalog": "user:new.json" },
              },
      };
    }),
  );
  render(
    <ChangesProvider>
    <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
      <SettingsProvider>
        <Choices />
      </SettingsProvider>
    </AccessContext.Provider>
    </ChangesProvider>,
  );
  fireEvent.click(await screen.findByText("Earlier save"));
  fireEvent.click(screen.getByText("Preview"));
  fireEvent.click(screen.getByText("Other Owner"));
  await screen.findByText("user:new.json");
  expect(screen.getByText("dark/larger")).toBeTruthy();
  fireEvent.click(screen.getByText("Finish setup"));
  await screen.findByText("refused");
  expect(screen.getByText("pending")).toBeTruthy();
  expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
  await act(async () => finish({ ...initial, revision: 1 }));
  await screen.findByText("earlier finished");
  expect(screen.getByText("dark/larger")).toBeTruthy();
  expect(screen.getByText("user:new.json")).toBeTruthy();
  expect(patches).toEqual([
    { set: { "camera.protocol": "http" } },
    { set: { "ui.quick_setup_done": true } },
  ]);
});
