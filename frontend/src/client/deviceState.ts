import type { SettingKey } from "../generated/settings";

/** A value a Device setting holds. */
export type DeviceValue = string | boolean;
/** An own choice and its local ordering stamp. */
export type Choice = { key: SettingKey; value: DeviceValue | null; stamp: number };
/** Why a displayed choice differs from the saved document. */
export type Entry = Omit<Choice, "key"> & { state: "preview" | "pending" | "unsaved" };
/** Saved state and the newest local intent for each key. */
export interface DeviceState {
  raw: Record<string, unknown>;
  local: ReadonlyMap<SettingKey, Entry>;
  clock: number;
  applied: ReadonlyMap<SettingKey, number>;
}
/** A persisted update; own writes never supersede unsaved local intent. */
export interface DeviceUpdate {
  values: Record<string, unknown>;
  foreign: readonly SettingKey[];
}
/** Keys whose raw values changed, shared by the two persistence owners. */
export function changedKeys(a: Record<string, unknown>, b: Record<string, unknown>): SettingKey[] {
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(
    (key) => JSON.stringify(a[key]) !== JSON.stringify(b[key]),
  ) as SettingKey[];
}
/** Show a new preview and supersede any older prepared choice. */
export function preview(s: DeviceState, key: SettingKey, value: DeviceValue): DeviceState {
  const stamp = s.clock + 1;
  return {
    ...s,
    clock: stamp,
    applied: new Map(s.applied).set(key, stamp),
    local: new Map(s.local).set(key, { value, state: "preview", stamp }),
  };
}
/** Reserve stamps without displaying or admitting the prepared choices. */
export function stamp(s: DeviceState, n: number): [DeviceState, number[]] {
  return [{ ...s, clock: s.clock + n }, Array.from({ length: n }, (_, i) => s.clock + i + 1)];
}
/** Admit only intents newer than the last own intent that took effect. */
export function admit(s: DeviceState, choices: readonly Choice[]): [DeviceState, Choice[]] {
  const local = new Map(s.local),
    applied = new Map(s.applied);
  const survivors = choices.filter((choice) => choice.stamp > (applied.get(choice.key) ?? 0));
  for (const { key, value, stamp } of survivors) {
    applied.set(key, stamp);
    local.set(key, { value, stamp, state: "pending" });
  }
  return [{ ...s, local, applied }, survivors];
}
/** Settle only entries still belonging to this batch, preserving newer intent. */
export function settle(
  s: DeviceState,
  choices: readonly Choice[],
  failed: readonly SettingKey[],
): DeviceState {
  const local = new Map(s.local);
  for (const choice of choices) {
    if (local.get(choice.key)?.stamp !== choice.stamp) continue;
    if (failed.includes(choice.key)) local.set(choice.key, { ...choice, state: "unsaved" });
    else local.delete(choice.key);
  }
  return { ...s, local };
}
/** Receive saved values without moving own stamps or cancelling pending intent. */
export function receive(s: DeviceState, update: DeviceUpdate): DeviceState {
  const local = new Map(s.local);
  for (const key of update.foreign) if (local.get(key)?.state !== "pending") local.delete(key);
  return { ...s, raw: update.values, local };
}
