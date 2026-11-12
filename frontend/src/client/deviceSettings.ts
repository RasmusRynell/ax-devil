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
  /** Stores this save's keys in one batch, returning all raw values after it; shell failures throw. */
  write(
    changes: Partial<Record<SettingKey, DeviceValue | null>>,
  ): Record<string, unknown> | Promise<Record<string, unknown>>;
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

/** Browser persistence: independent keys avoid lost updates; failed writes retain their current ephemeral values. */
export function browserBacking(
  context: Pick<Window, "localStorage" | "addEventListener" | "removeEventListener"> = window,
): Backing {
  let current: Record<string, unknown> = {};
  const ephemeral = new Map<string, { value: DeviceValue | null; observed: string | undefined }>();
  const read = (): Record<string, unknown> => {
    try {
      // The old shared entry is read-only fallback. A per-key null tombstone makes resets authoritative over it.
      const legacyText = context.localStorage.getItem(KEY);
      let legacy: unknown;
      try {
        legacy = JSON.parse(legacyText ?? "{}");
      } catch {
        // Invalid legacy JSON must not hide independent per-key saves.
        legacy = {};
      }
      const kept =
        typeof legacy === "object" && legacy !== null && !Array.isArray(legacy)
          ? ({ ...legacy } as Record<string, unknown>)
          : {};
      for (const { key } of DEVICE_ROWS) {
        const text = context.localStorage.getItem(`${KEY}.${key}`);
        if (text === null) continue;
        try {
          const value: unknown = JSON.parse(text);
          if (value === null) delete kept[key];
          else kept[key] = value;
        } catch {
          /* A malformed entry is retained as a recoverable preference. */ kept[key] = text;
        }
      }
      current = kept;
    } catch {
      /* Storage is blocked: keep the last current map. */
    }
    const shown = { ...current };
    for (const [key, { value }] of ephemeral) {
      if (value === null) delete shown[key];
      else shown[key] = value;
    }
    return shown;
  };
  return {
    read,
    write(changes) {
      read();
      for (const [key, value] of Object.entries(changes)) {
        try {
          context.localStorage.setItem(`${KEY}.${key}`, JSON.stringify(value));
          ephemeral.delete(key);
        } catch {
          ephemeral.set(key, { value, observed: JSON.stringify(current[key]) });
        }
      }
      return read();
    },
    subscribe(listener) {
      const changed = (event: StorageEvent) => {
        if (event.key === KEY || event.key?.startsWith(`${KEY}.`) || event.key === null) {
          read();
          // All event kinds observe current storage, never the delivery order of queued events.
          for (const [key, choice] of ephemeral)
            if (JSON.stringify(current[key]) !== choice.observed) ephemeral.delete(key);
          listener(read());
        }
      };
      context.addEventListener("storage", changed);
      return () => context.removeEventListener("storage", changed);
    },
  };
}

/** A Device persistence failure, naming only the keys that still need to be saved. */
export interface DeviceFailure {
  keys: SettingKey[];
  reason: string;
}

/** The Device values of one backing: the only place they are held. */
export function createDeviceStore(backing: Backing) {
  let raw = backing.read();
  let stored = valid(raw);
  let values = stored;
  const recoveredKeys = () =>
    DEVICE_ROWS.filter((row) => row.key in raw && !holds(row, raw[row.key])).map((row) => row.key);
  let recovered: SettingKey[] = recoveredKeys();
  // Retain #390's per-key submission/version model: preparation never writes, previews and confirmed changes supersede it.
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
  const receive = (
    incoming: Record<string, unknown>,
    choices: readonly (readonly [SettingKey, DeviceValue | null])[] = [],
    supersedes = true,
  ) => {
    const previous = raw;
    raw = incoming;
    stored = valid(raw);
    recovered = recoveredKeys();
    const own = new Map(choices);
    for (const { key } of DEVICE_ROWS) {
      const chosen = own.get(key);
      const applied =
        own.has(key) &&
        (chosen === null
          ? !(key in raw)
          : !recovered.includes(key) && chosen === (stored[key] ?? DEVICE_DEFAULTS.get(key)));
      if (applied) publish(key, stored[key] ?? null, supersedes);
      else if (own.has(key) || JSON.stringify(previous[key]) !== JSON.stringify(raw[key]))
        publish(key, stored[key] ?? null);
    }
  };
  const write = (
    changes: readonly (readonly [SettingKey, DeviceValue | null])[],
    supersedes = true,
    current = backing.read(),
    accepts?: (key: SettingKey) => boolean,
  ): DeviceFailure | null | Promise<DeviceFailure | null> => {
    const atDispatch = new Map(changes.map(([key]) => [key, versions.get(key) ?? 0]));
    const accepted =
      accepts ?? ((key: SettingKey) => (versions.get(key) ?? 0) === atDispatch.get(key));
    const patch: Partial<Record<SettingKey, DeviceValue | null>> = {};
    for (const [key, value] of changes) {
      const unchanged =
        value === null
          ? !(key in current)
          : DEVICE_ROWS.some((row) => row.key === key && holds(row, current[key]))
            ? value === current[key]
            : !(key in current) && value === DEVICE_DEFAULTS.get(key);
      if (!unchanged) patch[key] = value;
    }
    const finish = (next: Record<string, unknown>) => {
      const choices = changes.filter(([key]) => accepted(key));
      if (!choices.length) return null;
      const kept = { ...next };
      for (const [key] of changes) {
        if (accepted(key)) continue;
        if (key in raw) kept[key] = raw[key];
        else delete kept[key];
      }
      receive(kept, choices, supersedes);
      return null;
    };
    const failed = (error: unknown): DeviceFailure | null => {
      const keys = (Object.keys(patch) as SettingKey[]).filter(accepted);
      return keys.length
        ? { keys, reason: error instanceof Error ? error.message : String(error) }
        : null;
    };
    try {
      const next = Object.keys(patch).length ? backing.write(patch) : current;
      // Browser commits stay synchronous; a shell reply is checked again after asynchronous persistence.
      return next instanceof Promise
        ? next.then(() => finish(backing.read())).catch(failed)
        : finish(next);
    } catch (error) {
      return failed(error);
    }
  };
  backing.subscribe((incoming) => receive(incoming));
  return {
    /** The effective Device values, including unsaved Quick Setup previews. */
    read: () => values,
    /** Last confirmed values, excluding previews; backing writes remain best effort. */
    readSaved: () => stored,
    /** Raw invalid keys whose effective defaults can be explicitly repaired. */
    readRecovered: () => recovered,
    /** Previews one choice without writing it. Later choices for this key supersede it. */
    preview: (key: SettingKey, value: DeviceValue) => publish(key, value),
    /** Stores one value. */
    set: (key: SettingKey, value: DeviceValue) => {
      return write([[key, value]]);
    },
    /** Puts one value back to its default. */
    reset: (key: SettingKey) => {
      return write([[key, null]]);
    },
    /** Captures a pending write; committing it leaves any subsequently changed keys alone. */
    prepare: (set: [SettingKey, DeviceValue][], reset: SettingKey[]) => {
      const changes: [SettingKey, DeviceValue | null][] = [
        ...set,
        ...reset.map((key): [SettingKey, null] => [key, null]),
      ];
      // Per-key observations catch cross-tab confirmations before their storage event arrives. The confirmed
      // ordinal still permits this store's earlier submissions to complete without cancelling later ones.
      const observed = backing.read();
      const pending = changes.map(([key, value]) => {
        const ordinal = (submitted.get(key) ?? 0) + 1;
        submitted.set(key, ordinal);
        return {
          key,
          value,
          ordinal,
          version: versions.get(key) ?? 0,
          observed: JSON.stringify(observed[key]),
          confirmed: committed.get(key) ?? 0,
        };
      });
      return () => {
        const current = backing.read();
        const surviving = pending.filter(
          ({ key, ordinal, version, observed: value, confirmed }) =>
            (versions.get(key) ?? 0) === version &&
            ordinal > (committed.get(key) ?? 0) &&
            (JSON.stringify(current[key]) === value ||
              ((committed.get(key) ?? 0) > confirmed &&
                JSON.stringify(current[key]) === JSON.stringify(raw[key]))),
        );
        const accepts = (key: SettingKey) =>
          surviving.some(
            (choice) =>
              choice.key === key &&
              (versions.get(key) ?? 0) === choice.version &&
              choice.ordinal > (committed.get(key) ?? 0),
          );
        const failure = write(
          surviving.map(({ key, value }) => [key, value] as const),
          false,
          current,
          accepts,
        );
        const confirmed = (error: DeviceFailure | null) => {
          if (!error)
            for (const { key, ordinal } of surviving) if (accepts(key)) committed.set(key, ordinal);
          return error;
        };
        return failure instanceof Promise ? failure.then(confirmed) : confirmed(failure);
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
    write: async (changes) => {
      if (!bridge.setDeviceValues) throw new Error("This shell does not offer Device persistence");
      const reason = await bridge.setDeviceValues(changes);
      if (reason) throw new Error(reason);
      return bridge.deviceValues?.() ?? {};
    },
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

/** Device keys recovered from raw invalid preferences, independent of previews. */
export function useRecoveredDeviceKeys(): readonly SettingKey[] {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.readRecovered);
}
