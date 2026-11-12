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
