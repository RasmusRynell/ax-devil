/** The lanes of a viewer's entry: what each shows, how the user has set it, and what the backend says about it. */

import type { ClientMessage, LaneInfo, LaneStatusWire, LaneViewWire, TargetWire } from "./protocol";

/** One lane as the UI sees it. Replaced, never changed, when anything about the lane changes. */
export type LaneState = {
  label: string;
  /** False for the lane of an entry without overlays: there is nothing to load or join. */
  hasOverlay: boolean;
  status: LaneStatusWire | "plain";
  /** How the lane is shown now, as last sent to the backend. */
  view: LaneViewWire;
  /** Counts the lane's view changes; overlays carry the revision they were made for. */
  revision: number;
  /** Why the last view change could not be applied fully, until the next change. */
  problem?: string;
};

const sameTarget = (a: TargetWire, b: TargetWire): boolean =>
  a.width === b.width && a.height === b.height;

/** Whether two views show a lane the same way. */
export function sameLaneView(a: LaneViewWire, b: LaneViewWire): boolean {
  const keys = new Set([...Object.keys(a.filter), ...Object.keys(b.filter)]);
  return (
    a.catalog === b.catalog &&
    [...keys].every((key) => a.filter[key] === b.filter[key]) &&
    a.tolerance_ms === b.tolerance_ms &&
    a.sticky.enabled === b.sticky.enabled &&
    a.sticky.timeout_ms === b.sticky.timeout_ms &&
    a.sticky.opacity === b.sticky.opacity &&
    sameTarget(a.target, b.target) &&
    a.visible === b.visible
  );
}

/** The state of every lane of the opened entry, and the messages that change it. */
export class LaneSet {
  #states: readonly LaneState[] = [];
  /** The sizes the lanes were last asked to be displayed at, by lane. */
  #targets: readonly TargetWire[] = [];

  get states(): readonly LaneState[] {
    return this.#states;
  }

  /** Whether the lane's overlay has to be held before a frame is shown: it loaded and it is shown. */
  awaited(lane: number): boolean {
    const state = this.#states[lane];
    return (
      state !== undefined &&
      state.status !== "plain" &&
      state.status.status === "Ready" &&
      state.view.visible
    );
  }

  /**
   * Starts the lanes `Opened` describes. A reconnection to the same lanes keeps what the user set, and returns the
   * messages that give the new backend session those views again.
   */
  open(lanes: readonly LaneInfo[], defaults: LaneViewWire): ClientMessage[] {
    const before = this.#states;
    const same =
      before.length === lanes.length && before.every((s, i) => s.label === lanes[i].label);
    const resend: ClientMessage[] = [];
    this.#states = lanes.map((lane, index) => {
      const kept = same ? before[index] : undefined;
      const view = kept?.view ?? defaults;
      const revision = kept?.revision ?? 0;
      if (revision > 0) resend.push({ type: "LaneView", lane: index, revision, view });
      return {
        label: lane.label,
        hasOverlay: lane.has_overlay,
        status: lane.has_overlay ? { status: "Loading" } : "plain",
        view,
        revision,
      };
    });
    return [...resend, ...this.retarget()];
  }

  /** The backend's word on a lane's load. Reports whether anything changed. */
  setStatus(lane: number, status: LaneStatusWire): boolean {
    return this.#replace(lane, (state) => (state.hasOverlay ? { ...state, status } : state));
  }

  /** A problem with the lane's last view change. */
  setProblem(lane: number, problem: string): boolean {
    return this.#replace(lane, (state) => ({ ...state, problem }));
  }

  /**
   * Changes how a lane is shown: a new revision, and the message that tells the backend. Nothing when the change
   * leaves the view as it is.
   */
  setView(lane: number, change: Partial<LaneViewWire>): ClientMessage | undefined {
    const state = this.#states[lane];
    if (!state) return undefined;
    const view = { ...state.view, ...change };
    if (sameLaneView(view, state.view)) return undefined;
    const revision = state.revision + 1;
    this.#replace(lane, () => ({ ...state, view, revision, problem: undefined }));
    return { type: "LaneView", lane, revision, view };
  }

  /** Records the sizes lanes are displayed at (CSS pixels, by lane); `retarget` applies them. */
  setTargets(targets: readonly TargetWire[]): void {
    this.#targets = targets;
  }

  /** The view changes that bring each lane's target to the size it is displayed at. */
  retarget(): ClientMessage[] {
    const messages: ClientMessage[] = [];
    this.#targets.forEach((size, lane) => {
      const target = { width: Math.round(size.width), height: Math.round(size.height) };
      if (target.width < 1 || target.height < 1) return;
      const message = this.setView(lane, { target });
      if (message) messages.push(message);
    });
    return messages;
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
