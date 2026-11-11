import { useSyncExternalStore } from "react";
import { SETTINGS, type SettingKey } from "./generated/settings";
import type { WindowBridge } from "./windowBridge";

/** A value a Device setting holds. */
export type DeviceValue = string | boolean;
/** The Device settings that differ from their default. */
export type DeviceValues = Partial<Record<SettingKey, DeviceValue>>;

/** Where Device values are kept: the desktop shell's file for this device, or this browser's storage. */
interface Backing {
  /** Every stored value, as stored. */
  read(): Record<string, unknown>;
  /** Stores one value, or removes it with null; never throws. */
  write(key: string, value: DeviceValue | null): void;
  /** Calls `listener` with every value after another window or tab changed one. */
  subscribe(listener: (values: Record<string, unknown>) => void): () => void;
}

const DEVICE_ROWS = SETTINGS.filter((row) => row.scope === "device");

/** Whether *value* is something *row* can hold. */
function holds(row: (typeof DEVICE_ROWS)[number], value: unknown) {
  const { kind } = row;
  if (kind.type === "choice") return kind.options.some(([name]) => name === value);
  if (kind.type === "bool") return typeof value === "boolean";
  return kind.type === "text" && typeof value === "string";
}

/** The values of *stored* that a Device row accepts; an unknown key or an invalid value is left out. */
function valid(stored: Record<string, unknown>): DeviceValues {
  const values: DeviceValues = {};
  for (const row of DEVICE_ROWS) {
    const value = stored[row.key];
    if (holds(row, value)) values[row.key] = value as DeviceValue;
  }
  return values;
}

const KEY = "ax-devil.device";

/** This browser's storage, one key; a blocked or full storage keeps nothing and never throws. */
function browserBacking(): Backing {
  const read = (): Record<string, unknown> => {
    try {
      const kept: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "{}");
      return typeof kept === "object" && kept !== null ? (kept as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  };
  return {
    read,
    write(key, value) {
      const kept = read();
      if (value === null) delete kept[key];
      else kept[key] = value;
      try {
        window.localStorage.setItem(KEY, JSON.stringify(kept));
      } catch {
        // Nothing can be kept: the choice lasts until the page closes.
      }
    },
    subscribe(listener) {
      const changed = (event: StorageEvent) => {
        if (event.key === KEY || event.key === null) listener(read());
      };
      window.addEventListener("storage", changed);
      return () => window.removeEventListener("storage", changed);
    },
  };
}

/** The Device values of one backing: the only place they are held. */
export function createDeviceStore(backing: Backing) {
  let values = valid(backing.read());
  const listeners = new Set<() => void>();
  const replace = (next: DeviceValues) => {
    values = next;
    listeners.forEach((listener) => listener());
  };
  backing.subscribe((stored) => replace(valid(stored)));
  return {
    /** Every Device value that differs from its default; the same object until one changes. */
    read: () => values,
    /** Stores one value. */
    set: (key: SettingKey, value: DeviceValue) => {
      replace({ ...values, [key]: value });
      backing.write(key, value);
    },
    /** Puts one value back to its default. */
    reset: (key: SettingKey) => {
      const { [key]: _gone, ...rest } = values;
      replace(rest);
      backing.write(key, null);
    },
    /** Calls `listener` after every change, from here or from another window; returns the way to stop. */
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

/** The shell's file, through its three bridge functions; the shell and its frontend are versioned together. */
function shellBacking(bridge: WindowBridge): Backing {
  return {
    read: () => bridge.deviceValues?.() ?? {},
    write: (key, value) => bridge.setDeviceValue?.(key, value),
    subscribe: (listener) => bridge.onDeviceValuesChange?.(listener) ?? (() => {}),
  };
}

const bridge = window.axDevilWindow;
/** This device's settings: the desktop shell's file in the desktop app, browser storage in a browser. */
export const deviceStore = createDeviceStore(
  bridge?.deviceValues ? shellBacking(bridge) : browserBacking(),
);

/** The Device values, and a re-render when they change. */
export function useDeviceValues(): DeviceValues {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.read);
}
