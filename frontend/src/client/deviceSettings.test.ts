import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { browserBacking, createDeviceStore } from "./deviceSettings";

function kept(initial: Record<string, unknown> = {}) {
  let raw = { ...initial };
  let listener = (_values: Record<string, unknown>) => {};
  const write = vi.fn((changes: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) delete raw[key];
      else raw[key] = value;
    }
    return { ...raw };
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
    change: (next: Record<string, unknown>) => {
      raw = next;
      listener(next);
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("device settings", () => {
  it("falls back without discarding raw preferences, then explicitly repairs a default", () => {
    const { store, write } = kept({
      "ui.theme": { recoverable: "dark" },
      "ui.text_size": "large",
      future: 1,
    });
    expect(store.read()).toEqual({ "ui.text_size": "large" });
    expect(store.readRecovered()).toEqual(["ui.theme"]);
    store.set("ui.text_size", "small");
    expect(write).toHaveBeenCalledExactlyOnceWith({ "ui.text_size": "small" });
    store.prepare([["ui.theme", "auto"]], [])();
    expect(store.readRecovered()).toEqual([]);
    expect(store.readSaved()).toEqual({ "ui.theme": "auto", "ui.text_size": "small" });
  });

  it("merges external Device changes per key and protects newer choices from pending writes", () => {
    const { store, write, change } = kept();
    const commit = store.prepare(
      [
        ["ui.theme", "light"],
        ["ui.text_size", "large"],
      ],
      [],
    );
    store.preview("ui.theme", "dark");
    commit();
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "large" });
    expect(write).toHaveBeenCalledExactlyOnceWith({ "ui.text_size": "large" });
    change({ "ui.text_size": "small" });
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "small" });
    change({ "ui.theme": "light", "ui.text_size": "small" });
    expect(store.read()["ui.theme"]).toBe("light");
    store.preview("ui.theme", "dark");
    store.prepare([["ui.theme", "dark"]], [])();
    expect(write).toHaveBeenLastCalledWith({ "ui.theme": "dark" });
    const earlier = store.prepare([["ui.theme", "light"]], []);
    earlier();
    store.prepare([["ui.theme", "dark"]], [])();
    earlier();
    expect(store.readSaved()["ui.theme"]).toBe("dark");
  });

  it("replaces previews when confirming or resetting a stored choice without redundant writes", () => {
    const { store, write } = kept({ "ui.theme": "light" });
    store.preview("ui.theme", "dark");
    store.preview("ui.text_size", "larger");
    store.prepare(
      [
        ["ui.theme", "light"],
        ["ui.text_size", "system"],
      ],
      [],
    )();
    expect(store.read()).toEqual({ "ui.theme": "light" });
    expect(store.readSaved()).toEqual({ "ui.theme": "light" });
    expect(write).not.toHaveBeenCalled();
    store.preview("ui.text_size", "larger");
    store.prepare([], ["ui.text_size"])();
    expect(store.read()).toEqual({ "ui.theme": "light" });
    expect(write).not.toHaveBeenCalled();
  });

  it("reads a saved choice after reload despite malformed legacy JSON", () => {
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
    store.set("ui.theme", "dark");
    expect(store.readSaved()).toEqual({ "ui.theme": "dark" });
    const reloaded = createDeviceStore(browserBacking(context));
    expect(reloaded.read()).toEqual({ "ui.theme": "dark" });
    expect(data.get("ax-devil.device")).toBe("{broken");
  });

  it("keeps two successive ephemeral choices when storage is blocked", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    } as unknown as Storage;
    const store = createDeviceStore(
      browserBacking({
        localStorage: blocked,
        addEventListener: window.addEventListener.bind(window),
        removeEventListener: window.removeEventListener.bind(window),
      }),
    );
    store.set("ui.theme", "dark");
    store.set("ui.text_size", "large");
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "large" });
    expect(store.readSaved()).toEqual(store.read());
  });
});
