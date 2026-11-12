/** The Frame Maps a viewer holds: per lane and frame the sample that belongs to it, within a window. */

import { FRAME_MAP_RUN_FRAMES } from "../protocol";

/** A frame the viewer holds no map cell for. A sample index is 0 or more; the wire's NONE is -1 and EMPTY -2. */
export const UNKNOWN = -3;

type Page = { samples: Int32Array; kinds: Uint8Array };

type LaneMap = {
  policyRevision: number;
  dataRevision: number;
  pages: Map<number, Page>;
};

const newPage = (): Page => ({
  samples: new Int32Array(FRAME_MAP_RUN_FRAMES).fill(UNKNOWN),
  kinds: new Uint8Array(FRAME_MAP_RUN_FRAMES),
});

/**
 * The maps of every lane, in pages of 256 frames made when a run is written and dropped when the window leaves them.
 * A run is kept only for the revisions the lane currently expects and only inside the data window the backend applied, so what is held is
 * bounded by the window, not by the video.
 */
export class FrameMaps {
  readonly #frameCount: number;
  readonly #lanes = new Map<number, LaneMap>();
  #low = 0;
  #high = -1;

  constructor(frameCount: number) {
    this.#frameCount = frameCount;
  }

  /** The revisions a lane's runs must carry; a change forgets the lane's map. */
  setRevisions(lane: number, policyRevision: number, dataRevision: number): void {
    const held = this.#lanes.get(lane);
    if (held?.policyRevision === policyRevision && held.dataRevision === dataRevision) return;
    this.#lanes.set(lane, { policyRevision, dataRevision, pages: new Map() });
  }

  /** Keeps the cells of a run that carries the lane's revisions and lies inside the window; reports whether any. */
  add(
    lane: number,
    policyRevision: number,
    dataRevision: number,
    first: number,
    samples: Int32Array,
    kinds: Uint8Array,
  ): boolean {
    const held = this.#lanes.get(lane);
    if (held?.policyRevision !== policyRevision || held.dataRevision !== dataRevision) return false;
    const low = Math.max(first, this.#low);
    const high = Math.min(first + samples.length - 1, this.#high, this.#frameCount - 1);
    if (low > high) return false;
    const pageAt = Math.floor(first / FRAME_MAP_RUN_FRAMES);
    let page = held.pages.get(pageAt);
    if (!page) held.pages.set(pageAt, (page = newPage()));
    const start = pageAt * FRAME_MAP_RUN_FRAMES;
    for (let frame = low; frame <= high; frame++) {
      page.samples[frame - start] = samples[frame - first];
      page.kinds[frame - start] = kinds[frame - first];
    }
    return true;
  }

  /** The sample of a frame, `-1` (none), `-2` (empty), or `UNKNOWN`. */
  at(lane: number, frame: number): number {
    const page = this.#lanes.get(lane)?.pages.get(Math.floor(frame / FRAME_MAP_RUN_FRAMES));
    return page ? page.samples[frame % FRAME_MAP_RUN_FRAMES] : UNKNOWN;
  }

  /** How the frame's sample was found (`MATCH_*`); 0 when the cell is none, empty or unknown. */
  kind(lane: number, frame: number): number {
    const page = this.#lanes.get(lane)?.pages.get(Math.floor(frame / FRAME_MAP_RUN_FRAMES));
    return page ? page.kinds[frame % FRAME_MAP_RUN_FRAMES] : 0;
  }

  /** Forgets every cell outside `low..high`, for all lanes, and keeps accepting only that range. */
  retain(low: number, high: number): void {
    this.#low = low;
    this.#high = high;
    for (const { pages } of this.#lanes.values()) {
      for (const [pageAt, page] of pages) {
        const start = pageAt * FRAME_MAP_RUN_FRAMES;
        if (start + FRAME_MAP_RUN_FRAMES - 1 < low || start > high) {
          pages.delete(pageAt);
          continue;
        }
        for (let k = 0; k < FRAME_MAP_RUN_FRAMES; k++)
          if (start + k < low || start + k > high) page.samples[k] = UNKNOWN;
      }
    }
  }

  /** Forgets everything a lane holds, as when it is hidden; its revisions stay. */
  forget(lane: number): void {
    this.#lanes.get(lane)?.pages.clear();
  }
}
