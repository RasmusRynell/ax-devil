import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vite-plus/test";
import { createDeviceStore, shellBacking } from "./deviceSettings";
import type { DeviceReply, DeviceSnapshot } from "./windowBridge";

interface Owner {
  read(): DeviceSnapshot;
  write(changes: Record<string, unknown>, batch?: string): Promise<number>;
  subscribe(listener: (snapshot: DeviceSnapshot) => void): () => void;
}
interface PageWindow {
  webContents: { send: (channel: string, snapshot: DeviceSnapshot) => void };
}
interface Event {
  window: PageWindow;
  returnValue?: DeviceSnapshot;
}
const require = createRequire(import.meta.url);
const { createDeviceValues } = require("../../../desktop/device.cjs") as {
  createDeviceValues(this: void, file: string): Owner;
};
const { installDeviceIpc } = require("../../../desktop/device-ipc.cjs") as {
  installDeviceIpc(
    this: void,
    args: {
      ipcMain: {
        on(channel: string, handler: (event: Event) => void): void;
        handle(
          channel: string,
          handler: (
            event: Event,
            changes: Record<string, unknown>,
            batch: string,
          ) => Promise<DeviceReply>,
        ): void;
      };
      device: Owner;
      sender(event: Event): { window: PageWindow };
      windows(): PageWindow[];
    },
  ): void;
};
afterEach(() => vi.restoreAllMocks());

function shell(owner: Owner) {
  const windows: PageWindow[] = [];
  let read!: (event: Event) => void;
  let write!: (
    event: Event,
    changes: Record<string, unknown>,
    batch: string,
  ) => Promise<DeviceReply>;
  installDeviceIpc({
    device: owner,
    ipcMain: {
      on: (_channel, handler) => {
        read = handler;
      },
      handle: (_channel, handler) => {
        write = handler;
      },
    },
    sender: (event) => {
      if (!windows.includes(event.window)) throw new Error("Not allowed");
      return event;
    },
    windows: () => windows,
  });
  return () => {
    const listeners = new Set<(snapshot: DeviceSnapshot) => void>();
    const window = {
      webContents: {
        send: (_channel: string, snapshot: DeviceSnapshot) =>
          listeners.forEach((listener) => listener(snapshot)),
      },
    };
    windows.push(window);
    const bridge = {
      deviceValues: () => {
        const event: Event = { window };
        read(event);
        return event.returnValue!;
      },
      setDeviceValues: (changes: Record<string, unknown>, batch: string) =>
        write({ window }, changes, batch),
      onDeviceValuesChange: (listener: (snapshot: DeviceSnapshot) => void) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      },
    };
    return {
      bridge,
      window,
      reload: () => {
        listeners.clear();
        return createDeviceStore(shellBacking(bridge));
      },
    };
  };
}

for (const scenario of ["two windows", "replacement document"] as const) {
  it(`owner completion reaches ${scenario}`, { timeout: 5000 }, async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-owner-sequence-"));
    const file = path.join(folder, "device.json");
    fs.writeFileSync(file, JSON.stringify({ "ui.theme": "auto" }), { mode: 0o600 });
    const owner = createDeviceValues(file);
    const announcements: DeviceSnapshot[] = [];
    let completed!: () => void;
    const completion = new Promise<void>((resolve) => {
      completed = resolve;
    });
    owner.subscribe((snapshot) => {
      announcements.push(snapshot);
      completed();
    });
    const openWindow = shell(owner);
    const a = openWindow();
    const first = createDeviceStore(shellBacking(a.bridge));
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    let entered!: () => void;
    const started = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const rename = fs.promises.rename;
    let calls = 0;
    vi.spyOn(fs.promises, "rename").mockImplementation(async (...args) => {
      if (++calls === 1) {
        entered();
        await blocked;
      }
      return rename(...args);
    });
    const saved = first.prepare([["ui.theme", "dark"]], [])();
    try {
      await started;
      expect(owner.read()).toEqual({ values: { "ui.theme": "auto" }, sequence: 0 });
      if (scenario === "two windows") {
        const b = openWindow();
        const second = createDeviceStore(shellBacking(b.bridge));
        // Quick Setup retains its unsaved preview after refusal. Settings then explicitly
        // chooses the persisted System value, which must still reach the ordering owner.
        second.preview("ui.theme", "light");
        expect(second.read()["ui.theme"]).toBe("light");
        const earlier = second.prepare([["ui.theme", "light"]], []);
        const laterIntent = second.prepare([["ui.theme", "auto"]], []);
        // A's announcement must not cancel the later local prepared save (round 5 A).
        const middle = earlier();
        release();
        expect(await saved).toBeNull();
        expect(await middle).toBeNull();
        expect(await laterIntent()).toBeNull();
        // An own equal-value announcement also replaces a conflicting retained preview (B).
        second.preview("ui.theme", "dark");
        expect(await second.set("ui.theme", "auto")).toBeNull();
        expect(second.read()["ui.theme"]).toBe("auto");
        expect(first.readSaved()["ui.theme"]).toBe("auto");
        expect(second.readSaved()["ui.theme"]).toBe("auto");
        expect(JSON.parse(fs.readFileSync(file, "utf8"))).toEqual({ "ui.theme": "auto" });
        expect(owner.read()).toEqual({ values: { "ui.theme": "auto" }, sequence: 4 });
        expect(
          announcements.map(({ batch, ...snapshot }) => {
            expect(batch).toEqual(expect.any(String));
            return snapshot;
          }),
        ).toEqual([
          { values: { "ui.theme": "dark" }, sequence: 1 },
          { values: { "ui.theme": "light" }, sequence: 2 },
          { values: { "ui.theme": "auto" }, sequence: 3 },
          { values: { "ui.theme": "auto" }, sequence: 4 },
        ]);
        // Delivery of older or duplicate owner snapshots cannot regress the current value.
        b.window.webContents.send("device:changed", {
          values: { "ui.theme": "dark" },
          sequence: 1,
        });
        b.window.webContents.send("device:changed", {
          values: { "ui.theme": "dark" },
          sequence: 2,
        });
        expect(second.readSaved()["ui.theme"]).toBe("auto");
      } else {
        const replacement = a.reload();
        expect(replacement.readSaved()["ui.theme"]).toBe("auto");
        release();
        await completion;
        expect(replacement.readSaved()["ui.theme"]).toBe("dark");
        expect(a.bridge.deviceValues()).toEqual({ values: { "ui.theme": "dark" }, sequence: 1 });
      }
    } finally {
      release();
      if (scenario === "two windows") await saved;
      fs.rmSync(folder, { recursive: true, force: true });
    }
  });
}

it("refuses an old shell snapshot shape rather than interpreting it as preferences", () => {
  const store = createDeviceStore(
    shellBacking({ deviceValues: () => ({ "ui.theme": "dark" }) as unknown as DeviceSnapshot }),
  );
  expect(store.problem()).toContain("Incompatible desktop shell");
  expect(store.readSaved()).toEqual({});
});

it("saves through the shell when the secure-context UUID API is absent", async () => {
  vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-insecure-device-"));
  try {
    const owner = createDeviceValues(path.join(folder, "device.json"));
    const store = createDeviceStore(shellBacking(shell(owner)().bridge));
    expect(await store.set("ui.theme", "dark")).toBeNull();
    expect(owner.read().values).toEqual({ "ui.theme": "dark" });
    expect(store.readSaved()).toEqual({ "ui.theme": "dark" });
  } finally {
    vi.unstubAllGlobals();
    fs.rmSync(folder, { recursive: true, force: true });
  }
});
