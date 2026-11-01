/** The overlays a viewer holds for its lanes, with the rule that decides when a frame may be shown. */

import { AtlasMirror } from "../overlay/mirror";
import type { PreparedDrawing } from "../overlay/prepare";
import type { PreparedOverlay } from "../overlay/preparer";
import type { LaneSet } from "./lanes";
import { OverlayBuffer } from "./OverlayBuffer";
import type { Want } from "./protocol";

/** What a surface draws over a frame: each visible lane's overlay by lane, and the atlas page their texts sample. */
export type Composed = { overlays: readonly (PreparedDrawing | undefined)[]; atlas: AtlasMirror };

/**
 * The overlays of one entry's lanes, held for the frames of the window. A frame is shown only when every visible lane
 * that loaded has its overlay for it; a lane still loading, one that failed and one the user hid never hold a frame
 * back.
 */
export class LaneOverlays {
  readonly #lanes: LaneSet;
  #held: OverlayBuffer<PreparedDrawing> | undefined;
  /** The text atlas page as the worker of the current connection has it. */
  #atlas = new AtlasMirror();

  constructor(lanes: LaneSet) {
    this.#lanes = lanes;
  }

  /** A new connection: its worker starts with an empty atlas. */
  reconnect(): void {
    this.#atlas = new AtlasMirror();
  }

  /** Starts holding overlays for a video of `frameCount` frames. */
  open(frameCount: number): void {
    this.#held = new OverlayBuffer(frameCount);
  }

  /** Drops every held overlay. */
  close(): void {
    this.#held = undefined;
  }

  /** Keeps exactly the overlays of the window. */
  setWindow(window: Want): void {
    this.#held?.setWindow(window);
  }

  /** Drops what a lane holds, as when it is hidden. */
  forget(lane: number): void {
    this.#held?.forgetLane(lane);
  }

  /**
   * Takes the overlays the worker prepared and returns the sequence ids of those kept. Every drawing's atlas uploads
   * are applied, kept or not, so the page matches the worker's.
   */
  accept(overlays: readonly PreparedOverlay[]): number[] {
    const states = this.#lanes.states;
    const kept: number[] = [];
    for (const { lane, sequenceId, revision, prepared } of overlays) {
      this.#atlas.apply(prepared);
      if (states[lane]?.view.visible && this.#held?.add(lane, sequenceId, revision, prepared))
        kept.push(sequenceId);
    }
    return kept;
  }

  /** Whether every lane the frame waits on holds its overlay for it. */
  joined(sequenceId: number): boolean {
    return this.#lanes.states.every(
      (_, lane) => !this.#lanes.awaited(lane) || this.#held?.has(lane, sequenceId) === true,
    );
  }

  /** What to draw over the frame: the overlays of the visible lanes. */
  compose(sequenceId: number): Composed {
    const overlays = this.#lanes.states.map((state, lane) =>
      state.view.visible ? this.#held?.get(lane, sequenceId) : undefined,
    );
    return { overlays, atlas: this.#atlas };
  }
}
