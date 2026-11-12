import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { createDeviceStore } from "./deviceSettings";

const kept = (stored: Record<string, unknown>) =>
  createDeviceStore({ read: () => stored, write: () => {}, subscribe: () => () => {} });

afterEach(() => vi.unstubAllGlobals());

describe("device settings", () => {
  it("falls back to the default for an invalid or unknown stored value", () => {
    expect(
      kept({ "ui.theme": "neon", "ui.text_size": "large", "storage.cache_dir": "/x" }).read(),
    ).toEqual({ "ui.text_size": "large" });
  });

  it("merges external Device changes per key and protects newer choices from pending writes", () => {
    let changed: (values: Record<string, unknown>) => void = () => {};
    const write = vi.fn();
    const store = createDeviceStore({
      read: () => ({}),
      write,
      subscribe: (listener) => {
        changed = listener;
        return () => {};
      },
    });
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
    expect(write).toHaveBeenCalledExactlyOnceWith("ui.text_size", "large");
    changed({ "ui.text_size": "small" });
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "small" });
    changed({ "ui.theme": "light", "ui.text_size": "small" });
    expect(store.read()["ui.theme"]).toBe("light");
    store.preview("ui.theme", "dark");
    store.prepare([["ui.theme", "dark"]], [])();
    expect(write).toHaveBeenLastCalledWith("ui.theme", "dark");
    // The same ordering holds when Device commits precede a later save's submission/server reply.
    const earlier = store.prepare([["ui.theme", "light"]], []);
    earlier();
    store.prepare([["ui.theme", "dark"]], [])();
    earlier();
    expect(store.readSaved()["ui.theme"]).toBe("dark");
  });

  it("replaces previews when confirming or resetting a stored choice without redundant writes", () => {
    const write = vi.fn();
    const store = createDeviceStore({
      read: () => ({ "ui.theme": "light" }),
      write,
      subscribe: () => () => {},
    });
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

  it("changes in memory and tells its listeners even when the browser's storage is blocked", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    });
    vi.resetModules();
    const { deviceStore } = await import("./deviceSettings");
    const heard = vi.fn();
    deviceStore.subscribe(heard);
    expect(deviceStore.read()).toEqual({});
    deviceStore.set("ui.theme", "dark");
    expect(deviceStore.read()).toEqual({ "ui.theme": "dark" });
    expect(heard).toHaveBeenCalledTimes(1);
  });
});
