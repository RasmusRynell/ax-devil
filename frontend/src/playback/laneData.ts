/** What each lane draws on a frame: the sample its Frame Map assigns to it, or the reason there is none yet. */

import type { ClassMask } from "../present";
import type { LaneFrame } from "./contract";
import type { Catalogs } from "./catalogs";
import type { FrameMaps } from "./data/FrameMaps";
import { UNKNOWN } from "./data/FrameMaps";
import type { SampleStore } from "./data/SampleStore";
import type { LaneSet, ReadyStatus } from "./lanes";
import { MATCH_RETAINED, type FilterOptionInfo } from "./protocol";

/**
 * Draw the lane's marks; draw nothing, because the lane has none for the frame or is not shown; or wait, because the
 * data of the frame has not all arrived. A lane never draws another sample's marks.
 */
export type LaneDraw =
  | { state: "draw"; frame: LaneFrame }
  | { state: "none" }
  | { state: "pending" };

const NONE: LaneDraw = { state: "none" };
const PENDING: LaneDraw = { state: "pending" };

function maskOf(options: readonly FilterOptionInfo[], filter: Record<string, boolean>): ClassMask {
  const enabled = new Uint32Array(Math.ceil(options.length / 32));
  options.forEach((option, i) => {
    if (filter[option.id] ?? option.default_enabled) enabled[i >> 5] |= 1 << (i & 31);
  });
  return { enabled, options: options.length };
}

export class LaneData {
  readonly #lanes: LaneSet;
  readonly #samples: SampleStore;
  readonly #maps: FrameMaps;
  readonly #catalogs: Catalogs;
  /** The class mask of each lane, made again only when its options or toggles are replaced. */
  readonly #masks = new Map<number, { ready: ReadyStatus; filter: object; mask: ClassMask }>();

  constructor(lanes: LaneSet, samples: SampleStore, maps: FrameMaps, catalogs: Catalogs) {
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
    const entry = this.#catalogs.get(state.settings.catalog);
    if (entry.state === "loading") return PENDING;
    if (!entry.catalog) return NONE;
    return {
      state: "draw",
      frame: {
        source: source,
        dataRevision: ready.data_revision,
        sample: cell,
        columns,
        catalog: entry.catalog,
        styleRevision: this.#lanes.styleRevision(lane),
        classMask: this.#mask(lane, ready, state.settings.filter),
        filter: null,
        hidden: 0,
        theme: null,
        opacity:
          this.#maps.kind(lane, frame) === MATCH_RETAINED ? state.settings.sticky.opacity : 1,
      },
    };
  }

  /** Whether no shown lane waits for data of `frame`. */
  ready(frame: number): boolean {
    return this.#lanes.states.every((_, lane) => this.at(lane, frame).state !== "pending");
  }

  #mask(lane: number, ready: ReadyStatus, filter: Record<string, boolean>): ClassMask {
    const held = this.#masks.get(lane);
    if (held?.ready === ready && held.filter === filter) return held.mask;
    const mask = maskOf(ready.filter, filter);
    this.#masks.set(lane, { ready, filter, mask });
    return mask;
  }
}
