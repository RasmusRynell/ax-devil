/** The lanes of a viewer's entry: what each shows, how the user has set it, and what the backend says about it. */

import type { SettingsState } from "../generated/api";
import { MEASURES, OPEN_RANGES } from "../present";
import type { CatalogLook, LaneSettings, LaneState, Sticky } from "./contract";
import type {
  BasisWire,
  CatalogInfoWire,
  ClientMessage,
  LaneDefaultsWire,
  LaneInfo,
  LanePolicyWire,
  LaneStatusWire,
} from "./protocol";

export type { LaneSettings, LaneState } from "./contract";

/** The Ready status of a lane. */
export type ReadyStatus = Extract<LaneStatusWire, { status: "Ready" }>;

/** The basis `lane` matches by: its own setting when its file offers it, else the file's default; `undefined` until the file is ready. */
export function basisOf({ settings, status }: LaneState): BasisWire | undefined {
  if (status === "plain" || status.status !== "Ready") return undefined;
  return settings.basis !== null && status.bases.includes(settings.basis)
    ? settings.basis
    : status.default_basis;
}

/** How a lane matches samples to frames, which the workspace keeps with the lane. */
export const samePolicy = (a: LaneSettings, b: LaneSettings): boolean =>
  a.basis === b.basis &&
  a.tolerance_ms === b.tolerance_ms &&
  a.sticky.enabled === b.sticky.enabled &&
  a.sticky.timeout_ms === b.sticky.timeout_ms;

/** Whether two sticky settings are the same, opacity included. */
export const sameSticky = (a: Sticky, b: Sticky): boolean =>
  a.enabled === b.enabled && a.timeout_ms === b.timeout_ms && a.opacity === b.opacity;

/** What the backend needs of a lane: if any of it differs, the lane's Frame Maps are selected again. */
const sameSent = (a: LaneSettings, b: LaneSettings): boolean =>
  samePolicy(a, b) && a.visible === b.visible;

/** What changes the marks the lane draws from a sample, not which sample it is. */
function sameStyle(a: LaneSettings, b: LaneSettings): boolean {
  const keys = new Set([...Object.keys(a.filter), ...Object.keys(b.filter)]);
  return (
    a.catalog === b.catalog &&
    a.theme === b.theme &&
    a.hidden === b.hidden &&
    a.expression === b.expression &&
    MEASURES.every(
      (measure) =>
        a.ranges[measure].min === b.ranges[measure].min &&
        a.ranges[measure].max === b.ranges[measure].max,
    ) &&
    [...keys].every((key) => a.filter[key] === b.filter[key])
  );
}

/** The catalog `id` when the backend lists it and it compiled, else nothing: the one rule for what may be a default. */
export function usableCatalog(
  catalogs: readonly CatalogInfoWire[],
  id: unknown,
): CatalogInfoWire | undefined {
  const chosen = catalogs.find((catalog) => catalog.id === id);
  return chosen && chosen.problem === undefined ? chosen : undefined;
}

/**
 * The revision of the lane's chosen catalog that compiled and is still the newest the backend sent, else nothing
 * (loading, failed, deleted): the one rule for what may be copied, made the default or applied to the other lanes.
 */
export function judgedRevision({ catalogRevision, catalogProblem }: LaneState): number | undefined {
  return catalogProblem === undefined ? catalogRevision : undefined;
}

/** The catalog new views start with: the Personal setting when it is usable, else the first listed (Standard). */
export function effectiveDefault(catalogs: readonly CatalogInfoWire[], setting: unknown): string {
  return usableCatalog(catalogs, setting)?.id ?? catalogs[0]?.id ?? "";
}

/** How a lane's samples are held against its frames: how far back one may match, and how long one stays after. */
export type Timing = Pick<LaneSettings, "tolerance_ms" | "sticky">;

/** The Personal settings that hold the default timing. */
export const DEFAULT_SYNC = "overlays.default_sync";
export const DEFAULT_STICKY = "overlays.default_sticky";

/** The Personal default timing new lanes start with, offline and live alike. */
export const savedTiming = (values: SettingsState["values"]): Timing => ({
  tolerance_ms: (values[DEFAULT_SYNC] as { tolerance_ms: number | null }).tolerance_ms,
  sticky: values[DEFAULT_STICKY] as Sticky,
});

/**
 * What new lanes start with: the backend's defaults, but the Personal default catalog when it is usable and the
 * Personal default timing when there is one.
 */
export function startWith(
  defaults: LaneDefaultsWire,
  catalogs: readonly CatalogInfoWire[],
  personal: string | null | undefined,
  timing?: Timing,
): LaneSettings {
  const settings = settingsOf(defaults);
  return {
    ...settings,
    ...timing,
    kept_timeout_ms: timing?.sticky.timeout_ms ?? settings.kept_timeout_ms,
    kept_tolerance_ms: timing?.tolerance_ms ?? settings.kept_tolerance_ms,
    catalog: usableCatalog(catalogs, personal)?.id ?? settings.catalog,
  };
}

/** `settings` with the matching the workspace keeps for the lane, which wins over what the viewer carried. */
const withPolicy = (settings: LaneSettings, policy: LanePolicyWire): LaneSettings => ({
  ...settings,
  basis: policy.basis,
  tolerance_ms: policy.tolerance_ms,
  sticky: { ...policy.sticky, opacity: settings.sticky.opacity },
  kept_timeout_ms: policy.sticky.timeout_ms ?? settings.kept_timeout_ms,
  kept_tolerance_ms: policy.tolerance_ms ?? settings.kept_tolerance_ms,
});

/** The timeout a lane gets when the user limits a retention that has never had one. */
const DEFAULT_TIMEOUT_MS = 2050;
/** The tolerance a lane gets when the user switches from Exact to Previous and it has never had one. */
const DEFAULT_TOLERANCE_MS = 50;

const settingsOf = ({ catalog, policy }: LaneDefaultsWire): LaneSettings => ({
  catalog,
  theme: null,
  hidden: 0,
  filter: {},
  expression: "",
  ranges: OPEN_RANGES,
  basis: policy.basis,
  tolerance_ms: policy.tolerance_ms,
  sticky: { ...policy.sticky, opacity: 1 },
  kept_timeout_ms: policy.sticky.timeout_ms ?? DEFAULT_TIMEOUT_MS,
  kept_tolerance_ms: policy.tolerance_ms ?? DEFAULT_TOLERANCE_MS,
  visible: true,
});

/** The state of every lane of the opened entry, and the messages that change it. */
export class LaneSet {
  #states: readonly LaneState[] = [];
  readonly #onPolicy: (slot: string, policy: LanePolicyWire) => void;
  /** Counts the changes to what a lane's marks look like; the presenter caches by it. */
  #styles: number[] = [];

  /** `onPolicy` hears the matching of a lane whenever the user changes it, for the workspace to keep. */
  constructor(onPolicy: (slot: string, policy: LanePolicyWire) => void = () => {}) {
    this.#onPolicy = onPolicy;
  }

  get states(): readonly LaneState[] {
    return this.#states;
  }

  /** The lane's Ready status while it draws: it has a source, loaded and is shown. */
  awaited(lane: number): ReadyStatus | undefined {
    const state = this.#states[lane];
    if (state?.source == null || !state.settings.visible) return undefined;
    return state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;
  }

  styleRevision(lane: number): number {
    return this.#styles[lane] ?? 0;
  }

  /**
   * Starts the lanes `Opened` describes. A reconnection to the same lanes keeps what the user set; new lanes start
   * from `seeds` by position (the lanes of the entry before), else from `defaults`. The matching the workspace keeps for
   * a lane (`LaneInfo.policy`) wins over all of these. Every lane with a source gets a
   * new policy revision and its `LanePolicy`, which is also what subscribes it: backend lanes start unsubscribed.
   */
  open(
    lanes: readonly LaneInfo[],
    defaults: LaneSettings,
    seeds: readonly LaneSettings[] = [],
  ): ClientMessage[] {
    const before = this.#states;
    const same =
      before.length === lanes.length &&
      before.every((s, i) => s.label === lanes[i].label && s.source === lanes[i].source);
    const messages: ClientMessage[] = [];
    this.#styles = lanes.map((_, i) => (same ? (this.#styles[i] ?? 0) : 0));
    this.#states = lanes.map((lane, index) => {
      const kept = same ? before[index] : undefined;
      const carried = kept?.settings ?? seeds[index] ?? defaults;
      const settings = lane.policy ? withPolicy(carried, lane.policy) : carried;
      let policyRevision = kept?.policyRevision ?? 0;
      if (lane.source !== null) {
        policyRevision++;
        messages.push(this.#policy(index, policyRevision, settings));
      }
      return {
        label: lane.label,
        slot: lane.slot,
        source: lane.source,
        status: lane.source === null ? "plain" : { status: "Loading" },
        settings,
        policyRevision,
        pending: false,
      };
    });
    return messages;
  }

  /** The backend's word on a lane's load. Reports whether anything changed. */
  setStatus(lane: number, status: LaneStatusWire): boolean {
    return this.#replace(lane, (state) => (state.source === null ? state : { ...state, status }));
  }

  /** A problem the backend reported for the lane. */
  setProblem(lane: number, problem: string): boolean {
    return this.#replace(lane, (state) => ({ ...state, problem }));
  }

  /** Why the lane's catalog is not the chosen one; `undefined` when it is. */
  setCatalogProblem(lane: number, catalogProblem: string | undefined): boolean {
    return this.#replace(lane, (state) =>
      state.catalogProblem === catalogProblem ? state : { ...state, catalogProblem },
    );
  }

  /** What the catalog the lane draws with offers its menu; `undefined` when the lane has none. */
  setLook(lane: number, look: CatalogLook | undefined): boolean {
    return this.#replace(lane, (state) => (state.look === look ? state : { ...state, look }));
  }

  /** The revision of the document the lane's chosen catalog compiled from; `undefined` when none did. */
  setCatalogRevision(lane: number, revision: number | undefined): boolean {
    return this.#replace(lane, (state) =>
      state.catalogRevision === revision ? state : { ...state, catalogRevision: revision },
    );
  }

  /** Whether the lane waits for data of the frame on screen. */
  setPending(lane: number, pending: boolean): boolean {
    return this.#replace(lane, (state) =>
      state.pending === pending ? state : { ...state, pending },
    );
  }

  /**
   * Changes a lane's settings. The result is the `LanePolicy` to send when something the backend selects by changed,
   * which also numbers the change; nothing for a local setting or a change that changes nothing.
   */
  set(lane: number, change: Partial<LaneSettings>): ClientMessage | undefined {
    const state = this.#states[lane];
    if (!state) return undefined;
    const settings = { ...state.settings, ...change };
    const sent = state.source !== null && !sameSent(settings, state.settings);
    const style = !sameStyle(settings, state.settings);
    // Visibility is local to a lane without a source, which sends no policy but still has to be shown or hidden.
    if (
      !sent &&
      !style &&
      settings.sticky.opacity === state.settings.sticky.opacity &&
      settings.kept_timeout_ms === state.settings.kept_timeout_ms &&
      settings.kept_tolerance_ms === state.settings.kept_tolerance_ms &&
      settings.visible === state.settings.visible
    )
      return undefined;
    const policyRevision = sent ? state.policyRevision + 1 : state.policyRevision;
    if (style) this.#styles = this.#styles.map((n, i) => (i === lane ? n + 1 : n));
    this.#replace(lane, () => ({
      ...state,
      settings,
      policyRevision,
      problem: sent ? undefined : state.problem,
    }));
    if (!samePolicy(settings, state.settings))
      this.#onPolicy(state.slot, this.#wirePolicy(settings));
    return sent ? this.#policy(lane, policyRevision, settings) : undefined;
  }

  /** Gives every other lane with a source the settings of `from` but its visibility; the policies to send. */
  copyFrom(from: number): ClientMessage[] {
    const state = this.#states[from];
    if (!state) return [];
    const { visible: _visible, ...copied } = state.settings;
    return this.#states.flatMap((other, lane) => {
      const message = lane === from || other.source === null ? undefined : this.set(lane, copied);
      return message ? [message] : [];
    });
  }

  #wirePolicy({ basis, tolerance_ms, sticky }: LaneSettings): LanePolicyWire {
    return {
      basis,
      tolerance_ms,
      sticky: { enabled: sticky.enabled, timeout_ms: sticky.timeout_ms },
    };
  }

  #policy(lane: number, revision: number, settings: LaneSettings): ClientMessage {
    return {
      type: "LanePolicy",
      lane,
      revision,
      policy: this.#wirePolicy(settings),
      subscribed: settings.visible,
    };
  }

  #replace(lane: number, change: (state: LaneState) => LaneState): boolean {
    const state = this.#states[lane];
    if (!state) return false;
    const next = change(state);
    if (next === state) return false;
    this.#states = this.#states.map((held, index) => (index === lane ? next : held));
    return true;
  }
}
