/** What each lane draws on a frame: the sample its Frame Map assigns to it, or the reason there is none yet. */

import type { LaneStyleWire } from "../generated/api";
import type { LaneFrame, LaneSettings } from "./contract";
import type { LaneCatalogs } from "./laneCatalog";
import type { FrameMaps } from "./data/FrameMaps";
import { UNKNOWN } from "./data/FrameMaps";
import type { SampleStore } from "./data/SampleStore";
import { frameOf, styleOf } from "./laneStyle";
import type { LaneSet, ReadyStatus } from "./lanes";
import { MATCH_RETAINED } from "./protocol";

/**
 * Draw the lane's marks; draw nothing, because the lane has none for the frame or is not shown; or wait, because the
 * data of the frame has not all arrived. A lane never draws another sample's marks.
 */
type LaneDraw = { state: "draw"; frame: LaneFrame } | { state: "none" } | { state: "pending" };

/** How a lane's Frame Map assigned a sample to a frame: the sample (`null` for none) with its `MATCH_*` kind, its time on the video clock and its frame in its file. */
export type LaneMatch = {
  sample: number | null;
  kind: number;
  timeUs: number | null;
  frame: number | null;
};

const NONE: LaneDraw = { state: "none" };
const PENDING: LaneDraw = { state: "pending" };

export class LaneData {
  readonly #lanes: LaneSet;
  readonly #samples: SampleStore;
  readonly #maps: FrameMaps;
  readonly #catalogs: LaneCatalogs;
  /** The style of each lane, made again only when what it is made from changes. */
  readonly #styles = new Map<
    number,
    { ready: ReadyStatus; settings: LaneSettings; label: string; style: LaneStyleWire }
  >();

  constructor(lanes: LaneSet, samples: SampleStore, maps: FrameMaps, catalogs: LaneCatalogs) {
    this.#lanes = lanes;
    this.#samples = samples;
    this.#maps = maps;
    this.#catalogs = catalogs;
  }

  /** What `lane` draws on `frame`. */
  at(lane: number, frame: number): LaneDraw {
    const ready = this.#lanes.awaited(lane);
    const state = this.#lanes.states[lane];
    const source = state?.source;
    if (!ready || source == null) return NONE;
    const cell = this.#maps.at(lane, frame);
    if (cell === UNKNOWN) return PENDING;
    if (cell < 0) return NONE;
    const columns = this.#samples.get(source, cell);
    if (!columns) return PENDING;
    const chosen = this.#catalogs.resolve(lane, state.settings.catalog);
    if (chosen.pending) return PENDING;
    if (!chosen.catalog) return NONE;
    return {
      state: "draw",
      frame: frameOf(this.#style(lane, state.label, state.settings, ready), {
        source,
        dataRevision: ready.data_revision,
        sample: cell,
        columns,
        catalog: chosen.catalog,
        styleRevision: this.#lanes.styleRevision(lane),
        retained: this.#maps.kind(lane, frame) === MATCH_RETAINED,
      }),
    };
  }

  /** How `lane` matched a sample to `frame`; `null` for a lane without a source, or while the cell or the sample has not arrived. */
  match(lane: number, frame: number): LaneMatch | null {
    const source = this.#lanes.states[lane]?.source;
    if (!this.#lanes.awaited(lane) || source == null) return null;
    const sample = this.#maps.at(lane, frame);
    if (sample === UNKNOWN) return null;
    const kind = this.#maps.kind(lane, frame);
    if (sample < 0) return { sample: null, kind, timeUs: null, frame: null };
    const key = this.#samples.keyOf(source, sample);
    return key ? { sample, kind, ...key } : null;
  }

  /** Whether no shown lane waits for data of `frame`. */
  ready(frame: number): boolean {
    return this.#lanes.states.every((_, lane) => this.at(lane, frame).state !== "pending");
  }

  /** The lane's style; the same object until something it is made from changes, so the presenter keeps its caches. */
  #style(lane: number, label: string, settings: LaneSettings, ready: ReadyStatus): LaneStyleWire {
    const held = this.#styles.get(lane);
    // The file's filter options are fixed for a revision, so a status that only reports progress keeps the style.
    if (
      held?.ready.data_revision === ready.data_revision &&
      held.label === label &&
      held.settings.filter === settings.filter &&
      held.settings.hidden === settings.hidden &&
      held.settings.expression === settings.expression &&
      held.settings.theme === settings.theme &&
      held.settings.sticky.opacity === settings.sticky.opacity
    )
      return held.style;
    const style = styleOf(label, settings, ready.filter);
    this.#styles.set(lane, { ready, settings, label, style });
    return style;
  }
}
