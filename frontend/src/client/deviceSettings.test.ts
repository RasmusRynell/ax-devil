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

  it("merges external Device changes per key and protects newer choices from pending writes", async () => {
    const { store, write, change } = kept();
    const commit = store.prepare(
      [
        ["ui.theme", "light"],
        ["ui.text_size", "large"],
      ],
      [],
    );
    store.preview("ui.theme", "dark");
    await commit();
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "large" });
    expect(write).toHaveBeenCalledExactlyOnceWith({ "ui.text_size": "large" });
    change({ "ui.text_size": "small" });
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "small" });
    change({ "ui.theme": "light", "ui.text_size": "small" });
    expect(store.read()["ui.theme"]).toBe("light");
    store.preview("ui.theme", "dark");
    await store.prepare([["ui.theme", "dark"]], [])();
    expect(write).toHaveBeenLastCalledWith({ "ui.theme": "dark" });
    const earlier = store.prepare([["ui.theme", "light"]], []);
    await earlier();
    await store.prepare([["ui.theme", "dark"]], [])();
    await earlier();
    expect(store.readSaved()["ui.theme"]).toBe("dark");
  });

  it("replaces previews when confirming or resetting a stored choice without redundant writes", async () => {
    const { store, write } = kept({ "ui.theme": "light" });
    store.preview("ui.theme", "dark");
    store.preview("ui.text_size", "larger");
    await store.prepare(
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
    await store.prepare([], ["ui.text_size"])();
    expect(store.read()).toEqual({ "ui.theme": "light" });
    expect(write).not.toHaveBeenCalled();
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
    "keeps a newer ephemeral choice through a queued %s event, until a newer stored choice arrives",
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
      expect(store.readSaved()["ui.theme"]).toBe("dark");
      if (kind === "clear") data.clear();
      else data.set(key, encode("auto"));
      events.dispatchEvent(
        new StorageEvent("storage", { key: eventKey, newValue: encode("auto") }),
      );
      expect(store.readSaved()["ui.theme"] ?? "auto").toBe("auto");
    },
  );

  it("keeps two successive ephemeral choices when storage is blocked", async () => {
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
    await store.set("ui.theme", "dark");
    await store.set("ui.text_size", "large");
    expect(store.read()).toEqual({ "ui.theme": "dark", "ui.text_size": "large" });
    expect(store.readSaved()).toEqual(store.read());
  });
});

it("a late asynchronous reply cannot overwrite a superseding confirmed choice", async () => {
  let raw: Record<string, unknown> = {};
  const replies: ((values: Record<string, unknown>) => void)[] = [];
  const store = createDeviceStore({
    read: () => raw,
    subscribe: () => () => {},
    write: () => new Promise<Record<string, unknown>>((resolve) => replies.push(resolve)),
  });
  const old = store.prepare([["ui.theme", "dark"]], [])();
  const newer = store.prepare([["ui.theme", "light"]], [])();
  expect(store.readSaved()).toEqual({});
  raw = { "ui.theme": "light" };
  replies[1](raw);
  await newer;
  expect(store.readSaved()).toEqual(raw);
  const published = vi.fn();
  store.subscribe(published);
  replies[0]({ "ui.theme": "dark" });
  await old;
  expect(published).not.toHaveBeenCalled();
  expect(store.readSaved()).toEqual({ "ui.theme": "light" });
  expect(store.read()).toEqual({ "ui.theme": "light" });
});
