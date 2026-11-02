/** The lanes of a viewer's entry: what each shows, how the user has set it, and what the backend says about it. */

import type { CatalogLook, LaneSettings, LaneState } from "./contract";
import type {
  CatalogInfoWire,
  ClientMessage,
  LaneDefaultsWire,
  LaneInfo,
  LaneStatusWire,
} from "./protocol";

export type { LaneSettings, LaneState } from "./contract";

/** The Ready status of a lane. */
export type ReadyStatus = Extract<LaneStatusWire, { status: "Ready" }>;

/** What the backend needs of a lane: if any of it differs, the lane's Frame Maps are selected again. */
const sameSent = (a: LaneSettings, b: LaneSettings): boolean =>
  a.tolerance_ms === b.tolerance_ms &&
  a.sticky.enabled === b.sticky.enabled &&
  a.sticky.timeout_ms === b.sticky.timeout_ms &&
  a.visible === b.visible;

/** What changes the marks the lane draws from a sample, not which sample it is. */
function sameStyle(a: LaneSettings, b: LaneSettings): boolean {
  const keys = new Set([...Object.keys(a.filter), ...Object.keys(b.filter)]);
  return (
    a.catalog === b.catalog &&
    a.theme === b.theme &&
    a.hidden === b.hidden &&
    a.expression === b.expression &&
    [...keys].every((key) => a.filter[key] === b.filter[key])
  );
}

/** What new lanes start with: the Personal default catalog when the backend lists it, else the backend's own. */
export function startWith(
  defaults: LaneDefaultsWire,
  catalogs: readonly CatalogInfoWire[],
  personal: string | null | undefined,
): LaneDefaultsWire {
  return personal && catalogs.some((catalog) => catalog.id === personal)
    ? { ...defaults, catalog: personal }
    : defaults;
}

const settingsOf = ({ catalog, policy }: LaneDefaultsWire): LaneSettings => ({
  catalog,
  theme: null,
  hidden: 0,
  filter: {},
  expression: "",
  tolerance_ms: policy.tolerance_ms,
  sticky: { ...policy.sticky, opacity: 1 },
  visible: true,
});

/** The state of every lane of the opened entry, and the messages that change it. */
export class LaneSet {
  #states: readonly LaneState[] = [];
  /** Counts the changes to what a lane's marks look like; the presenter caches by it. */
  #styles: number[] = [];

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
   * from `seeds` by position (the lanes of the entry before), else from `defaults`. Every lane with a source gets a
   * new policy revision and its `LanePolicy`, which is also what subscribes it: backend lanes start unsubscribed.
   */
  open(
    lanes: readonly LaneInfo[],
    defaults: LaneDefaultsWire,
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
      const settings = kept?.settings ?? seeds[index] ?? settingsOf(defaults);
      let policyRevision = kept?.policyRevision ?? 0;
      if (lane.source !== null) {
        policyRevision++;
        messages.push(this.#policy(index, policyRevision, settings));
      }
      return {
        label: lane.label,
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

  #policy(lane: number, revision: number, settings: LaneSettings): ClientMessage {
    const { tolerance_ms, sticky, visible } = settings;
    return {
      type: "LanePolicy",
      lane,
      revision,
      policy: { tolerance_ms, sticky: { enabled: sticky.enabled, timeout_ms: sticky.timeout_ms } },
      subscribed: visible,
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
