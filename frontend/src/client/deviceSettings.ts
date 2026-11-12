import { useSyncExternalStore } from "react";
import { SETTINGS, type SettingKey } from "../generated/settings";
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
const DEVICE_DEFAULTS = new Map<SettingKey, unknown>(
  DEVICE_ROWS.map((row) => [row.key, "default" in row.kind ? row.kind.default : undefined]),
);

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
  let stored = valid(backing.read());
  let values = stored;
  // Save commits follow per-key submission order without invalidating later submissions, while previews, direct writes and incoming broadcasts invalidate pending saves independently.
  const versions = new Map<SettingKey, number>();
  const submitted = new Map<SettingKey, number>();
  const committed = new Map<SettingKey, number>();
  const listeners = new Set<() => void>();
  const publish = (key: SettingKey, value: DeviceValue | null, supersedes = true) => {
    if (supersedes) versions.set(key, (versions.get(key) ?? 0) + 1);
    values = { ...values };
    if (value === null) delete values[key];
    else values[key] = value;
    listeners.forEach((listener) => listener());
  };
  const write = (key: SettingKey, value: DeviceValue | null, supersedes = true) => {
    // Confirming an already stored choice must still replace its runtime preview, without another backing write.
    const unchanged =
      value === null
        ? stored[key] === undefined
        : value === (stored[key] ?? DEVICE_DEFAULTS.get(key));
    if (unchanged) {
      publish(key, stored[key] ?? null, supersedes);
      return;
    }
    stored = { ...stored };
    if (value === null) delete stored[key];
    else stored[key] = value;
    publish(key, value, supersedes);
    backing.write(key, value);
  };
  backing.subscribe((incoming) => {
    const next = valid(incoming);
    // A broadcast carries the whole file: only keys changed in it supersede a local preview.
    const previous = stored;
    stored = next;
    for (const { key } of DEVICE_ROWS)
      if (next[key] !== previous[key]) publish(key, next[key] ?? null);
  });
  return {
    /** The effective Device values, including unsaved Quick Setup previews. */
    read: () => values,
    /** Last confirmed values, excluding previews; backing writes remain best effort. */
    readSaved: () => stored,
    /** Previews one choice without writing it. Later choices for this key supersede it. */
    preview: (key: SettingKey, value: DeviceValue) => publish(key, value),
    /** Stores one value. */
    set: (key: SettingKey, value: DeviceValue) => write(key, value),
    /** Puts one value back to its default. */
    reset: (key: SettingKey) => write(key, null),
    /** Captures a pending write; committing it leaves any subsequently changed keys alone. */
    prepare: (set: [SettingKey, DeviceValue][], reset: SettingKey[]) => {
      const changes: [SettingKey, DeviceValue | null][] = [
        ...set,
        ...reset.map((key): [SettingKey, null] => [key, null]),
      ];
      const pending = changes.map(([key, value]) => {
        const ordinal = (submitted.get(key) ?? 0) + 1;
        submitted.set(key, ordinal);
        return { key, value, ordinal, version: versions.get(key) ?? 0 };
      });
      return () => {
        for (const { key, value, version, ordinal } of pending) {
          if ((versions.get(key) ?? 0) !== version || ordinal <= (committed.get(key) ?? 0))
            continue;
          committed.set(key, ordinal);
          write(key, value, false);
        }
      };
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

/** Device values confirmed by a save or broadcast, without unsaved previews. */
export function useSavedDeviceValues(): DeviceValues {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.readSaved);
}
