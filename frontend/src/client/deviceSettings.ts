import { useSyncExternalStore } from "react";
import { SETTINGS, type SettingKey } from "../generated/settings";
import type { DeviceSnapshot, WindowBridge } from "./windowBridge";
import {
  admit,
  changedKeys,
  preview,
  receive,
  settle,
  stamp,
  type DeviceState,
  type DeviceUpdate,
  type DeviceValue,
} from "./deviceState";
export type { DeviceValue } from "./deviceState";

/** The Device settings that differ from their default. */
export type DeviceValues = Partial<Record<SettingKey, DeviceValue>>;
/** A Device persistence failure, naming only the keys that still need saving. */
export interface DeviceFailure {
  keys: SettingKey[];
  reason: string;
}
type Changes = Partial<Record<SettingKey, DeviceValue | null>>;
interface Backing {
  problem?: string;
  read(): Record<string, unknown>;
  subscribe(listener: (update: DeviceUpdate) => void): () => void;
  write(changes: Changes): Promise<DeviceFailure | null>;
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

/** Independent browser keys; failed writes remain local unsaved choices in the store. */
export function browserBacking(
  context: Pick<Window, "localStorage" | "addEventListener" | "removeEventListener"> = window,
): Backing {
  let current: Record<string, unknown> = {};
  const listeners = new Set<(update: DeviceUpdate) => void>();
  const emit = (foreign: readonly SettingKey[]) => {
    for (const listener of listeners) listener({ values: current, foreign });
  };
  const refresh = () => {
    try {
      const legacyText = context.localStorage.getItem(KEY);
      let legacy: unknown;
      try {
        legacy = JSON.parse(legacyText ?? "{}");
      } catch {
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
          kept[key] = text;
        }
      }
      const foreign = changedKeys(current, kept);
      current = kept;
      if (foreign.length) emit(foreign);
    } catch {
      /* Storage access is blocked: retain the last observed saved document. */
    }
  };
  refresh();
  return {
    read: () => current,
    async write(changes) {
      refresh();
      const failed: SettingKey[] = [];
      let reason = "";
      const next = { ...current };
      for (const [key, value] of Object.entries(changes)) {
        try {
          context.localStorage.setItem(`${KEY}.${key}`, JSON.stringify(value));
          if (value === null) delete next[key];
          else next[key] = value;
        } catch (error) {
          failed.push(key as SettingKey);
          reason = error instanceof Error ? error.message : String(error);
        }
      }
      current = next;
      emit([]);
      // Observe interleaved writes to other independent keys, without reinstalling a saved snapshot.
      refresh();
      return failed.length ? { keys: failed, reason } : null;
    },
    subscribe(listener) {
      listeners.add(listener);
      const changed = (event: StorageEvent) => {
        if (event.key === KEY || event.key?.startsWith(`${KEY}.`) || event.key === null) refresh();
      };
      context.addEventListener("storage", changed);
      return () => {
        listeners.delete(listener);
        context.removeEventListener("storage", changed);
      };
    },
  };
}

/** Device saved values and local intents, with snapshots derived once per state transition. */
export function createDeviceStore(backing: Backing) {
  let state: DeviceState = { raw: backing.read(), local: new Map(), clock: 0, applied: new Map() };
  const listeners = new Set<() => void>();
  const derive = () => {
    const saved = valid(state.raw);
    const values = { ...saved };
    for (const [key, entry] of state.local) {
      if (entry.value === null) delete values[key];
      else values[key] = entry.value;
    }
    const recovered = DEVICE_ROWS.filter(
      (row) => row.key in state.raw && !holds(row, state.raw[row.key]),
    ).map((row) => row.key);
    return { saved, values, recovered };
  };
  let cached = derive();
  const update = (next: DeviceState) => {
    state = next;
    cached = derive();
    listeners.forEach((listener) => listener());
  };
  backing.subscribe((incoming) => update(receive(state, incoming)));
  const prepare = (set: [SettingKey, DeviceValue][], reset: SettingKey[]) => {
    const changes: [SettingKey, DeviceValue | null][] = [
      ...set,
      ...reset.map((key): [SettingKey, null] => [key, null]),
    ];
    const [next, stamps] = stamp(state, changes.length);
    update(next);
    const choices = changes.map(([key, value], i) => ({ key, value, stamp: stamps[i]! }));
    return async () => {
      const [next, surviving] = admit(state, choices);
      if (!surviving.length) return null;
      update(next);
      const patch = Object.fromEntries(surviving.map(({ key, value }) => [key, value]));
      // Backing boundary: thrown APIs and rejected IPC must settle local intent too.
      let failure: DeviceFailure | null;
      try {
        failure = await backing.write(patch);
      } catch (error) {
        failure = {
          keys: surviving.map(({ key }) => key),
          reason: error instanceof Error ? error.message : String(error),
        };
      }
      update(settle(state, surviving, failure?.keys ?? []));
      return failure;
    };
  };
  return {
    /** Effective choices, including previews and unsaved admitted choices. */
    read: () => cached.values,
    /** An incompatible startup snapshot prevents settings from opening. */
    problem: () => backing.problem,
    /** Last persisted values, never local intents. */
    readSaved: () => cached.saved,
    /** Invalid raw preference keys. */
    readRecovered: () => cached.recovered,
    /** Display a new unsaved preview. */
    preview: (key: SettingKey, value: DeviceValue) => update(preview(state, key, value)),
    /** Admit one explicit choice. */
    set: (key: SettingKey, value: DeviceValue) => prepare([[key, value]], [])(),
    /** Admit one reset. */
    reset: (key: SettingKey) => prepare([], [key])(),
    /** Reserve own-intent ordering without writing or displaying a choice. */
    prepare,
    /** Subscribe to derived snapshots. */
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

/** Shell snapshots ordered by the owner; batch identities distinguish own updates. */
export function shellBacking(
  bridge: Pick<WindowBridge, "deviceValues" | "setDeviceValues" | "onDeviceValuesChange">,
): Backing {
  const validSnapshot = (value: unknown): DeviceSnapshot => {
    const snapshot = value as DeviceSnapshot | null;
    if (
      !snapshot ||
      !Number.isSafeInteger(snapshot.sequence) ||
      snapshot.sequence < 0 ||
      !snapshot.values ||
      typeof snapshot.values !== "object" ||
      Array.isArray(snapshot.values)
    )
      throw new Error(
        "Incompatible desktop shell: update and restart the desktop app (Device snapshot interface 10 required).",
      );
    return snapshot;
  };
  let snapshot: DeviceSnapshot = { values: {}, sequence: 0 };
  let problem: string | undefined;
  try {
    snapshot = validSnapshot(bridge.deviceValues?.());
  } catch (error) {
    problem = error instanceof Error ? error.message : String(error);
  }
  let nonce: string | undefined;
  let counter = 0;
  const mine = new Map<string, () => void>();
  return {
    problem,
    read: () => snapshot.values,
    write: async (changes) => {
      const keys = Object.keys(changes) as SettingKey[];
      if (problem || !bridge.setDeviceValues)
        return { keys, reason: problem ?? "This shell does not offer Device persistence" };
      // getRandomValues is available on plain HTTP too; identity needs uniqueness, not secrecy.
      nonce ??= Array.from(crypto.getRandomValues(new Uint32Array(4)), (value) =>
        value.toString(16),
      ).join("-");
      const batch = `${nonce}:${++counter}`;
      let delivered!: () => void;
      const completion = new Promise<void>((resolve) => {
        delivered = resolve;
      });
      mine.set(batch, delivered);
      try {
        const reply = await bridge.setDeviceValues(changes, batch);
        if (reply.reason) return { keys, reason: reply.reason };
        if (reply.sequence === null || reply.sequence > snapshot.sequence) await completion;
        return null;
      } finally {
        mine.delete(batch);
      }
    },
    subscribe: (listener) =>
      bridge.onDeviceValuesChange?.((incoming) => {
        const next = validSnapshot(incoming);
        if (next.sequence <= snapshot.sequence) return;
        const own = next.batch ? mine.get(next.batch) : undefined;
        const foreign = own ? [] : changedKeys(snapshot.values, next.values);
        snapshot = next;
        listener({ values: snapshot.values, foreign });
        own?.();
      }) ?? (() => {}),
  };
}

const bridge = window.axDevilWindow;
/** This device's settings: shell storage in the app, independent browser keys otherwise. */
export const deviceStore = createDeviceStore(bridge ? shellBacking(bridge) : browserBacking());
/** Effective Device choices and updates. */
export function useDeviceValues(): DeviceValues {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.read);
}
/** Persisted Device choices and updates. */
export function useSavedDeviceValues(): DeviceValues {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.readSaved);
}
/** Invalid raw Device keys, independent of local choices. */
export function useRecoveredDeviceKeys(): readonly SettingKey[] {
  return useSyncExternalStore(deviceStore.subscribe, deviceStore.readRecovered);
}
