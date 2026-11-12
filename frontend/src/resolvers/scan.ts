import { reasonOf } from "../client/errors";
import type { ChoiceInfo, ErrorWire, ScanCountInfo, ScanRequest } from "../generated/api";
import { ask, type Answer } from "../client/ask";

/** Resolver settings as the backend takes them. */
export type Settings = Record<string, unknown>;

/** Finds the values of a `Scan` field from the other settings. Aborting the signal cancels the scan. */
export function scanChoices(
  resolver: string,
  field: string,
  settings: Settings,
  signal: AbortSignal,
): Promise<Answer<ChoiceInfo[]>> {
  const request: ScanRequest = { field, settings };
  return ask<ChoiceInfo[]>(`/api/resolvers/${encodeURIComponent(resolver)}/scan`, {
    json: request,
    signal,
  });
}

/** What a `Scan` field shows and keeps. */
export interface ScanView {
  phase: "idle" | "scanning" | "done" | "failed";
  /** The values the scan found, in its order. */
  choices: readonly ChoiceInfo[];
  /** Saved values the scan did not find; they stay in the list, marked as not found. */
  missing: readonly string[];
  /** The values that will load: found ones and saved ones alike. */
  selected: ReadonlySet<string>;
  /** Whether the next scan checks every value it finds: no list is saved. */
  everything: boolean;
  /** Whether loading every found value omits the key: restored settings that named no list. A new form lists its names. */
  leaveOut: boolean;
  failure: ErrorWire | null;
}

/** A field that has not scanned. `saved` is the list the settings hold for it, if any; `restored` that they were saved. */
export function idleScan(saved: unknown, restored = false): ScanView {
  const names = Array.isArray(saved) ? saved.filter((name) => typeof name === "string") : null;
  return {
    phase: "idle",
    choices: [],
    missing: [],
    selected: new Set(names ?? []),
    everything: names === null,
    leaveOut: restored && names === null,
    failure: null,
  };
}

/** The field while a scan runs: the old list is gone. */
export function scanStarted(view: ScanView): ScanView {
  return { ...view, phase: "scanning", choices: [], missing: [], failure: null };
}

/** The field after a settings change it depends on: the list and the saved selection are gone, so the next scan checks everything. */
export function scanInvalidated(view: ScanView): ScanView {
  return {
    ...view,
    phase: "idle",
    choices: [],
    missing: [],
    selected: new Set(),
    everything: true,
    leaveOut: false,
    failure: null,
  };
}

/** The field after its scan found `choices`. Everything is selected when no list was saved. */
export function scanFinished(view: ScanView, choices: readonly ChoiceInfo[]): ScanView {
  if (view.everything) {
    return {
      ...view,
      phase: "done",
      choices,
      missing: [],
      selected: new Set(choices.map((choice) => choice.value)),
    };
  }
  const found = new Set(choices.map((choice) => choice.value));
  return {
    ...view,
    phase: "done",
    choices,
    missing: [...view.selected].filter((name) => !found.has(name)),
  };
}

/** The field after its scan was refused. */
export function scanFailed(view: ScanView, failure: ErrorWire): ScanView {
  return { ...view, phase: "failed", choices: [], missing: [], failure };
}

/** What the status line of a count-only scan says; `ready` is whether every setting the scan reads is filled in. */
export function countStatus(
  view: ScanView | undefined,
  count: ScanCountInfo,
  ready: boolean,
): string {
  switch (view?.phase) {
    case "scanning":
      return "Scanning…";
    case "failed":
      return `Scan failed: ${view.failure ? reasonOf(view.failure) : ""}`;
    case "done":
      return view.choices.length === 0
        ? count.none
        : count.found.replaceAll("{count}", String(view.choices.length));
    default:
      return ready ? "" : count.missing;
  }
}

/** Every value the list shows: found ones, then saved ones that were not found. */
export function listed(view: ScanView): string[] {
  return [...view.choices.map((choice) => choice.value), ...view.missing];
}

/** The field with `value` checked or unchecked. */
export function toggled(view: ScanView, value: string): ScanView {
  const selected = new Set(view.selected);
  if (!selected.delete(value)) selected.add(value);
  return { ...view, selected };
}

/** The field with everything checked, or with nothing checked when everything was. */
export function toggledAll(view: ScanView): ScanView {
  const all = listed(view);
  const every = all.every((value) => view.selected.has(value));
  return { ...view, selected: new Set(every ? [] : all) };
}

/** How many of the listed values are checked. */
export function checkedCount(view: ScanView): number {
  return listed(view).filter((value) => view.selected.has(value)).length;
}

/** What the settings hold for the field: the checked values, or `undefined` for the key to be left out. */
export function chosen(view: ScanView): string[] | undefined {
  const all = listed(view);
  if (view.leaveOut && all.every((value) => view.selected.has(value))) return undefined;
  return all.filter((value) => view.selected.has(value));
}
