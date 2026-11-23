import { useEffect, useRef, useState } from "react";
import type { PlaybackView } from "./view";

/**
 * The frame picked: held while the pointer `pointer` is down on the seek bar, then, with `pointer` null, while its seek
 * is pending. `from` is the frame on screen at the release, `playing` whether the clock ran then.
 */
type Held = { frame: number; pointer: number | null; from?: number; playing?: boolean };

/**
 * What ends a released pick: its frame is on screen or failed, the screen moved on from the frame it showed at
 * release, playback started or stopped since (a pause drops the seek that was pending, and shows nothing new), or,
 * paused, the playhead left the pick's frame (a step or seek made elsewhere superseded it, and a paused playhead
 * never moves on its own).
 */
type Progress = Pick<PlaybackView, "shown" | "frameError" | "playing" | "playhead">;

const settled = (pick: Held, { shown, frameError, playing, playhead }: Progress) =>
  pick.pointer === null &&
  (shown?.sequenceId === pick.frame ||
    shown?.sequenceId !== pick.from ||
    frameError?.sequenceId === pick.frame ||
    playing !== pick.playing ||
    (!playing && playhead !== pick.frame));

/**
 * The frame picked on the seek bar, which the readouts show instead of the frame on screen. The drag only moves the
 * pick; playback goes on, and the one seek is made when the pointer that pressed is let go (or the press ends in a
 * cancel or lost focus), as the reference does. After it the pick stays until its frame is on screen or fails, delivery
 * shows another frame, or playback starts or stops, and so never outlives a seek that goes wrong; a new press, `clear` or a new player
 * ends it too (a press ends only with its own pointer or blur). A change with no press, such as a key, seeks at once.
 */
export function useScrub(
  player: { seek(frame: number): void },
  progress: Progress,
): {
  picked: number | null;
  /** The pointer `pointer` went down on the bar while it showed `frame`; a press already held ignores another. */
  press: (frame: number, pointer: number) => void;
  /** The bar changed to `frame`. */
  change: (frame: number) => void;
  /** Ends a press now, with its seek; does nothing without one. */
  release: () => void;
  /** Another seek is made: drops a pick that waits for its own, never a press still held. */
  clear: () => void;
} {
  const [pick, setPick] = useState<Held | null>(null);
  // The pick again, readable by the window listeners and by a second end of the same press, which must not seek twice.
  const current = useRef<Held | null>(null);
  const now = useRef(progress);
  const pointer = pick?.pointer ?? null;

  const set = (next: Held | null) => {
    current.current = next;
    setPick(next);
  };

  const release = () => {
    const held = current.current;
    if (held?.pointer == null) return;
    set({
      frame: held.frame,
      pointer: null,
      from: now.current.shown?.sequenceId,
      playing: now.current.playing,
    });
    player.seek(held.frame);
  };

  useEffect(() => {
    now.current = progress;
    if (pick && settled(pick, progress)) set(null);
  });

  useEffect(() => {
    if (pointer === null) return;
    const end = (event: PointerEvent) => {
      if (event.pointerId === pointer) release();
    };
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    return () => {
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
    // `release` reads the pick from its ref and seeks on `player`, so the listeners last as long as the press.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [pointer, player]);

  // Another player leaves no pick of the old one behind.
  useEffect(() => () => set(null), [player]);

  return {
    picked: pick && !settled(pick, progress) ? pick.frame : null,
    press: (frame, id) => {
      if (current.current?.pointer == null) set({ frame, pointer: id });
    },
    change: (frame) => {
      const held = current.current;
      if (held?.pointer == null) {
        set(null);
        player.seek(frame);
      } else set({ ...held, frame });
    },
    release,
    clear: () => {
      if (current.current?.pointer === null) set(null);
    },
  };
}
