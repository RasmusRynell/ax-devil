import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AccessContext } from "./access";
import { browserBacking, createDeviceStore, deviceStore, shellBacking } from "./deviceSettings";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import { reasonOf } from "./errors";
import { SettingsProvider, useSettings } from "./settings";

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  await deviceStore.reset("ui.theme");
  await deviceStore.reset("ui.text_size");
  window.localStorage.clear();
  window.dispatchEvent(new StorageEvent("storage", { key: null }));
});
const { createDeviceValues } = createRequire(import.meta.url)("../../../desktop/device.cjs") as {
  createDeviceValues: (file: string) => {
    read(): { values: Record<string, unknown>; sequence: number };
    write(changes: object, batch?: string): Promise<number>;
    subscribe(
      listener: (snapshot: { values: Record<string, unknown>; sequence: number }) => void,
    ): () => void;
  };
};

function Save({ both = true }: { both?: boolean }) {
  const { apply, values } = useSettings();
  const [reason, setReason] = useState("");
  return (
    <>
      <button
        onClick={() =>
          void apply({
            set: {
              "ui.theme": "dark",
              ...(both && { "ui.text_size": "large" }),
              "ui.quick_setup_done": true,
              "playback.video_cache": 512,
            },
          }).then((error) => setReason(error ? reasonOf(error) : "saved"))
        }
      >
        Save
      </button>
      <output aria-label="Choices">
        {JSON.stringify([values["ui.theme"], values["ui.text_size"]])}
      </output>
      {reason && <p role="alert">{reason}</p>}
    </>
  );
}

async function openSave(both = true) {
  let release!: (response: Response) => void;
  const reply = new Promise<Response>((resolve) => {
    release = resolve;
  });
  let requested!: () => void;
  const sent = new Promise<void>((resolve) => {
    requested = resolve;
  });
  vi.stubGlobal("fetch", (_url: unknown, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      requested();
      return reply;
    }
    return Promise.resolve(new Response(JSON.stringify(DEFAULT_SETTINGS)));
  });
  render(
    <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
      <SettingsProvider>
        <Save both={both} />
      </SettingsProvider>
    </AccessContext.Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await sent;
  return async (accepted: boolean) => {
    await act(async () =>
      release(
        accepted
          ? new Response(
              JSON.stringify({ ...DEFAULT_SETTINGS, revision: DEFAULT_SETTINGS.revision + 1 }),
            )
          : new Response(
              JSON.stringify({ code: "InvalidRequest", params: {}, reason: "Server refused" }),
              { status: 400 },
            ),
      ),
    );
    return screen.findByRole("alert");
  };
}

it.each(["refused", "device fails", "saved", "pending"])(
  "Server-first shell save: %s",
  async (outcome) => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-provider-"));
    const file = path.join(folder, "device.json");
    const raw = { "ui.theme": { recoverable: "light" }, unrelated: 17 };
    fs.writeFileSync(file, JSON.stringify(raw), { mode: 0o600 });
    const writer = createDeviceValues(file);
    const mine = createDeviceStore(
      shellBacking({
        deviceValues: () => writer.read(),
        onDeviceValuesChange: (listener) => writer.subscribe(listener),
        async setDeviceValues(changes, batch) {
          try {
            return { sequence: await writer.write(changes, batch), reason: null };
          } catch (error) {
            return { sequence: null, reason: String(error) };
          }
        },
      }),
    );
    vi.spyOn(deviceStore, "prepare").mockImplementation(mine.prepare);
    const rename = vi.spyOn(fs.promises, "rename");
    try {
      const answer = await openSave();
      expect(rename).not.toHaveBeenCalled();
      expect(JSON.parse(fs.readFileSync(file, "utf8"))).toEqual(raw);
      if (outcome === "pending") {
        // An unanswered PATCH must not reserve the shell's Device writer.
        await writer.write({ "ui.theme": "light" });
        expect(writer.read().values["ui.theme"]).toBe("light");
        return;
      }
      if (outcome === "device fails")
        rename.mockImplementation(() => {
          throw new Error("Device refused");
        });
      const alert = await answer(outcome !== "refused");
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      if (outcome === "saved") {
        expect(rename).toHaveBeenCalledTimes(1);
        expect(writer.read().values).toEqual({
          ...raw,
          "ui.theme": "dark",
          "ui.text_size": "large",
        });
        expect(alert.textContent).toBe("saved");
      } else {
        expect(JSON.parse(fs.readFileSync(file, "utf8"))).toEqual(raw);
        expect(writer.read().values).toEqual(raw);
        if (outcome === "refused") {
          expect(alert.textContent).toBe("Server refused");
          expect(rename).not.toHaveBeenCalled();
        } else {
          expect(alert.textContent).toContain("Server settings were saved.");
          expect(alert.textContent).toContain(
            "Device settings were not saved: ui.theme, ui.text_size",
          );
        }
      }
    } finally {
      fs.rmSync(folder, { recursive: true, force: true });
    }
  },
);

it.each([null, "light"])(
  "two production browser contexts preserve an interleaved reset/change (%s)",
  async (choice) => {
    const data = new Map([
      ["ax-devil.device", JSON.stringify({ "ui.theme": "dark", future: { raw: true } })],
    ]);
    let interleave: (() => void) | undefined;
    const context = (first: boolean) => {
      const events = new EventTarget();
      const storage: Storage = {
        get length() {
          return data.size;
        },
        clear: () => data.clear(),
        removeItem: (key) => {
          data.delete(key);
        },
        key: (at) => [...data.keys()][at] ?? null,
        getItem: (key) => data.get(key) ?? null,
        setItem(key, value) {
          if (first && interleave) {
            const next = interleave;
            interleave = undefined;
            next();
          }
          data.set(key, value);
        },
      };
      return {
        events,
        backing: browserBacking({
          localStorage: storage,
          addEventListener: events.addEventListener.bind(events) as Window["addEventListener"],
          removeEventListener: events.removeEventListener.bind(
            events,
          ) as Window["removeEventListener"],
        }),
      };
    };
    const a = context(true),
      b = context(false);
    const tabA = createDeviceStore(a.backing),
      tabB = createDeviceStore(b.backing);
    interleave = () => {
      if (choice === null) void tabB.reset("ui.theme");
      else void tabB.set("ui.theme", choice);
    };
    vi.spyOn(deviceStore, "prepare").mockImplementation(tabA.prepare);
    const answer = await openBatch(false);
    await answer(true);
    b.events.dispatchEvent(new StorageEvent("storage", { key: "ax-devil.device.ui.text_size" }));
    const expected = { ...(choice !== null && { "ui.theme": choice }), "ui.text_size": "large" };
    expect(tabA.readSaved()).toEqual(expected);
    expect(tabB.readSaved()).toEqual(expected);
    expect(a.backing.read()).toEqual({ ...expected, future: { raw: true } });
  },
);

function Batch({ both = true }: { both?: boolean }) {
  const { apply, values } = useSettings();
  const [notice, setNotice] = useState("");
  return (
    <>
      <button
        onClick={() =>
          void apply({
            set: {
              "camera.protocol": "http",
              "ui.text_size": "large",
              ...(both && { "ui.theme": "dark" }),
            },
          }).then((error) => setNotice(error ? reasonOf(error) : "saved"))
        }
      >
        Batch
      </button>
      <p>{`${values["ui.theme"] as string}/${values["ui.text_size"] as string}`}</p>
      {notice && <p role="alert">{notice}</p>}
    </>
  );
}

async function openBatch(both = true) {
  let release!: (reply: Response) => void;
  const reply = new Promise<Response>((resolve) => {
    release = resolve;
  });
  let asked!: () => void;
  const requested = new Promise<void>((resolve) => {
    asked = resolve;
  });
  vi.stubGlobal("fetch", (_url: unknown, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      asked();
      return reply;
    }
    return Promise.resolve(new Response(JSON.stringify(DEFAULT_SETTINGS)));
  });
  render(
    <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
      <SettingsProvider>
        <Batch both={both} />
      </SettingsProvider>
    </AccessContext.Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Batch" }));
  await requested;
  return async (accepted: boolean) => {
    await act(async () =>
      release(
        accepted
          ? new Response(
              JSON.stringify({ ...DEFAULT_SETTINGS, revision: DEFAULT_SETTINGS.revision + 1 }),
            )
          : new Response(
              JSON.stringify({ code: "InvalidRequest", params: {}, reason: "Server refused" }),
              { status: 400 },
            ),
      ),
    );
    return screen.findByRole("alert");
  };
}
