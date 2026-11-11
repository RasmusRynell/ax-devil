import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "./access";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import { ChangesProvider, useNoticeSink } from "./resource";
import type { SettingsPatch } from "../generated/api";
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

function DefaultChoice({ id, validate }: { id: string; validate: () => Promise<number> }) {
  const { saveCatalogDefault } = useSettings();
  return <button onClick={() => void saveCatalogDefault(id, validate)}>{id}</button>;
}

function Timing() {
  const { set, values } = useSettings();
  return (
    <>
      <button onClick={() => void set("overlays.default_sync", { tolerance_ms: 80 })}>
        Save timing
      </button>
      <output>
        {JSON.stringify([values["overlays.default_catalog"], values["overlays.default_sync"]])}
      </output>
    </>
  );
}

describe("settings", () => {
  it("keeps a pending timing save and the newest catalog default across detached menus and delayed replies", async () => {
    let finishA!: (revision: number) => void;
    let finishB!: (revision: number) => void;
    const a = new Promise<number>((resolve) => {
      finishA = resolve;
    });
    const b = new Promise<number>((resolve) => {
      finishB = resolve;
    });
    let releaseTiming!: () => void;
    const timingReply = new Promise<void>((resolve) => {
      releaseTiming = resolve;
    });
    let stored = DEFAULT_SETTINGS;
    const writes: SettingsPatch[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method) {
          expect(init.method).toBe("PATCH");
          const patch = JSON.parse(init.body as string) as SettingsPatch;
          writes.push(patch);
          stored = {
            ...stored,
            revision: stored.revision + 1,
            values: { ...stored.values, ...patch.set } as typeof stored.values,
          };
        }
        // Commit the first write but hold its old response until the second has been submitted.
        const answer = stored;
        if (writes.length === 1) await timingReply;
        return { ok: true, json: async () => answer };
      }),
    );
    const menu = (showA: boolean) => (
      <AccessContext value={{ owner: true, fullAccess: false }}>
        <ChangesProvider><SettingsProvider>
          {showA && <DefaultChoice id="user:a.json" validate={() => a} />}
          <DefaultChoice id="user:b.json" validate={() => b} />
          <Timing />
        </SettingsProvider></ChangesProvider>
      </AccessContext>
    );
    const { rerender } = render(menu(true));
    fireEvent.click(await screen.findByRole("button", { name: "user:a.json" }));
    rerender(menu(false));
    fireEvent.click(screen.getByRole("button", { name: "user:b.json" }));
    fireEvent.click(screen.getByRole("button", { name: "Save timing" }));
    await act(async () => {
      finishB(2);
    });
    expect(writes).toEqual([
      { set: { "overlays.default_sync": { tolerance_ms: 80 } } },
      { set: { "overlays.default_catalog": "user:b.json" }, catalogRevision: 2 },
    ]);
    await act(async () => {
      releaseTiming();
      finishA(1);
    });
    expect(writes).toHaveLength(2);
    expect(stored.values).toMatchObject({
      "overlays.default_catalog": "user:b.json",
      "overlays.default_sync": { tolerance_ms: 80 },
    });
    expect(screen.getByRole("status").textContent).toBe('["user:b.json",{"tolerance_ms":80}]');
  });


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
  const { values, revision, apply, previewDevice } = useSettings();
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
      <button onClick={() => noticed?.("settings", 1, false)}>Own save notice</button>
      <p>{`revision ${revision}`}</p>
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

it.each(["before reply", "after reply"])(
  "keeps previews through refusal and Owner Changed when the own notice arrives %s",
  async (noticeOrder) => {
    let finish: (state: unknown) => void = () => {};
    const earlier = new Promise((resolve) => {
      finish = resolve;
    });
    const { "ui.theme": _theme, "ui.text_size": _size, ...values } = DEFAULT_SETTINGS.values;
    const initial = { ...DEFAULT_SETTINGS, values: { ...values, "ui.quick_setup_done": false } };
    let served = initial;
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
                json: async () => ({
                  code: "Internal",
                  params: {},
                  reason: "disk full",
                  location: "ui.quick_setup_done",
                }),
              };
        }
        return { ok: true, json: async () => served };
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
    const otherOwner = async () => {
      served = {
        ...initial,
        revision: 5,
        values: { ...initial.values, "overlays.default_catalog": "user:new.json" },
      };
      fireEvent.click(screen.getByText("Other Owner"));
      await screen.findByText("user:new.json");
      expect(screen.getByText("dark/larger")).toBeTruthy();
    };
    if (noticeOrder === "before reply") {
      served = { ...initial, revision: 1 };
      fireEvent.click(screen.getByText("Own save notice"));
      await screen.findByText("revision 1");
      expect(screen.getByText("dark/larger")).toBeTruthy();
      await otherOwner();
    }
    fireEvent.click(screen.getByText("Finish setup"));
    await screen.findByText("refused");
    expect(screen.getByText("pending")).toBeTruthy();
    expect(window.localStorage.getItem("ax-devil.device")).toBeNull();
    await act(async () => finish({ ...initial, revision: 1 }));
    await screen.findByText("earlier finished");
    if (noticeOrder === "after reply") {
      served = { ...initial, revision: 1 };
      fireEvent.click(screen.getByText("Own save notice"));
      await screen.findByText("revision 1");
      expect(screen.getByText("dark/larger")).toBeTruthy();
      await otherOwner();
    }
    expect(screen.getByText("dark/larger")).toBeTruthy();
    expect(screen.getByText("user:new.json")).toBeTruthy();
    expect(patches).toEqual([
      { set: { "camera.protocol": "http" } },
      { set: { "ui.quick_setup_done": true } },
    ]);
  },
);
