import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { DeviceUpdate } from "./deviceState";
import { browserBacking, createDeviceStore } from "./deviceSettings";

function kept(initial: Record<string, unknown> = {}) {
  let raw = { ...initial };
  let listener = (_values: DeviceUpdate) => {};
  const write = vi.fn(async (changes: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) delete raw[key];
      else raw[key] = value;
    }
    listener({ values: { ...raw }, foreign: [] });
    return null;
  });
  const store = createDeviceStore({
    read: () => ({ ...raw }),
    write,
    subscribe: (next) => {
      listener = next;
      return () => {};
    },
  });
  return {
    store,
    write,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("device settings", () => {
  it("falls back without discarding raw preferences, then explicitly repairs a default", async () => {
    const { store, write } = kept({
      "ui.theme": { recoverable: "dark" },
      "ui.text_size": "large",
      future: 1,
    });
    expect(store.read()).toEqual({ "ui.text_size": "large" });
    expect(store.readRecovered()).toEqual(["ui.theme"]);
    await store.set("ui.text_size", "small");
    expect(write).toHaveBeenCalledExactlyOnceWith({ "ui.text_size": "small" });
    await store.prepare([["ui.theme", "auto"]], [])();
    expect(store.readRecovered()).toEqual([]);
    expect(store.readSaved()).toEqual({ "ui.theme": "auto", "ui.text_size": "small" });
  });

  it("reads a saved choice after reload despite malformed legacy JSON", async () => {
    const data = new Map([["ax-devil.device", "{broken"]]);
    const context = {
      localStorage: {
        getItem: (key: string) => data.get(key) ?? null,
        setItem: (key: string, value: string) => data.set(key, value),
      } as unknown as Storage,
      addEventListener: window.addEventListener.bind(window),
      removeEventListener: window.removeEventListener.bind(window),
    };
    const store = createDeviceStore(browserBacking(context));
    await store.set("ui.theme", "dark");
    expect(store.readSaved()).toEqual({ "ui.theme": "dark" });
    const reloaded = createDeviceStore(browserBacking(context));
    expect(reloaded.read()).toEqual({ "ui.theme": "dark" });
    expect(data.get("ax-devil.device")).toBe("{broken");
  });

  it.each(["key", "clear", "legacy"])(
    "keeps a newer unsaved choice through a queued %s event, until a newer stored choice arrives",
    async (kind) => {
      const key = kind === "legacy" ? "ax-devil.device" : "ax-devil.device.ui.theme";
      const encode = (theme: string) =>
        JSON.stringify(kind === "legacy" ? { "ui.theme": theme } : theme);
      const data = new Map([[key, encode("light")]]);
      const events = new EventTarget();
      const backing = browserBacking({
        localStorage: {
          getItem: (name: string) => data.get(name) ?? null,
          setItem: () => {
            throw new Error("full");
          },
        } as unknown as Storage,
        addEventListener: events.addEventListener.bind(events) as Window["addEventListener"],
        removeEventListener: events.removeEventListener.bind(
          events,
        ) as Window["removeEventListener"],
      });
      const store = createDeviceStore(backing);
      await store.set("ui.theme", "dark");
      const eventKey = kind === "clear" ? null : key;
      events.dispatchEvent(
        new StorageEvent("storage", { key: eventKey, newValue: encode("light") }),
      );
      expect(store.read()["ui.theme"]).toBe("dark");
      expect(store.readSaved()["ui.theme"]).toBe("light");
      if (kind === "clear") data.clear();
      else data.set(key, encode("auto"));
      events.dispatchEvent(
        new StorageEvent("storage", { key: eventKey, newValue: encode("auto") }),
      );
      expect(store.readSaved()["ui.theme"] ?? "auto").toBe("auto");
    },
  );
});
