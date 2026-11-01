/**
 * The worker's overlay preparation for one viewer: it keeps the Drawing Instructions of the overlays the main
 * thread holds, so that when the text atlas starts a new page every one of them is prepared again on it and the
 * window never draws glyphs from a page that is gone.
 */

import type { TextAtlas } from "./atlas";
import type { DecodedInstructions } from "./instructions";
import { prepareDrawing, type PreparedDrawing } from "./prepare";

/** Which overlay a drawing belongs to. */
export type OverlayKey = { lane: number; sequenceId: number; revision: number };

/** An overlay to prepare: its key, its instructions and how it is drawn. */
export type OverlayInput = OverlayKey & {
  instructions: DecodedInstructions;
  target: { width: number; height: number };
  dpr: number;
  opacity: number;
};

/** A prepared overlay for the main thread. */
export type PreparedOverlay = OverlayKey & { prepared: PreparedDrawing };

/** Prepares overlays and prepares the held ones again after an atlas reset. */
export class OverlayPreparer {
  readonly #atlas: TextAtlas;
  /** Per lane, per sequence id, the newest revision. */
  readonly #held = new Map<number, Map<number, OverlayInput>>();

  constructor(atlas: TextAtlas) {
    this.#atlas = atlas;
  }

  /**
   * Prepares `input` and holds it. Returns its drawing first; when the atlas started a new page meanwhile, every
   * other held overlay follows, prepared again, to replace the drawing the main thread has for it. Post them
   * together and apply their uploads in this order. An input older than the held revision returns nothing.
   */
  prepare(input: OverlayInput): PreparedOverlay[] {
    let lane = this.#held.get(input.lane);
    if (!lane) {
      lane = new Map();
      this.#held.set(input.lane, lane);
    }
    const held = lane.get(input.sequenceId);
    if (held && held.revision > input.revision) return [];
    lane.set(input.sequenceId, input);
    const atlas = this.#atlas;
    const generation = atlas.generation;
    const out = [this.#prepared(input)];
    if (atlas.generation === generation) return out;
    // The window may hold more glyphs than a page: whatever does not fit now is left out, not reset again.
    const dropping = atlas.dropOverflow;
    atlas.dropOverflow = true;
    try {
      for (const overlays of this.#held.values())
        for (const overlay of overlays.values())
          if (overlay !== input) out.push(this.#prepared(overlay));
    } finally {
      atlas.dropOverflow = dropping;
    }
    return out;
  }

  /** Forgets the overlays of frames outside `first` to `last` (inclusive) on every lane. */
  retain(first: number, last: number): void {
    for (const overlays of this.#held.values())
      for (const sequenceId of overlays.keys())
        if (sequenceId < first || sequenceId > last) overlays.delete(sequenceId);
  }

  /** Forgets a lane's overlays. */
  forgetLane(lane: number): void {
    this.#held.delete(lane);
  }

  /** How many overlays are held. */
  get size(): number {
    let size = 0;
    for (const overlays of this.#held.values()) size += overlays.size;
    return size;
  }

  #prepared(input: OverlayInput): PreparedOverlay {
    const { lane, sequenceId, revision, instructions, target, dpr, opacity } = input;
    const prepared = prepareDrawing(instructions, target, dpr, this.#atlas, opacity);
    return { lane, sequenceId, revision, prepared };
  }
}
