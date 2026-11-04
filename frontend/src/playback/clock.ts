/**
 * The frontend presentation clock. The frontend owns the clock and the backend only sees its position as `Want`s.
 * Time comes from the injected `now()` (milliseconds); the caller calls `tick` on every display refresh.
 */

import type { ClockIntent } from "../generated/api";

export type { ClockIntent };

export const MIN_SPEED = 0.01;
export const MAX_SPEED = 10;
export const SPEED_STEP = 0.1;
/** The slowest speed a step reaches. */
export const SPEED_FLOOR = 0.1;

/** Speed rounded to hundredths and clamped to 0.01..10. */
export function clampSpeed(speed: number): number {
  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(speed * 100) / 100));
}

/** One 0.1 step up (`1`) or down (`-1`) from `speed`; never below 0.1, which a speed under it steps up to. */
export function steppedSpeed(speed: number, direction: 1 | -1): number {
  if (direction > 0 && speed < SPEED_FLOOR) return SPEED_FLOOR;
  return Math.max(SPEED_FLOOR, clampSpeed(speed + direction * SPEED_STEP));
}

/** The last frame whose time is at or before `timeUs`; 0 for a time before the first frame. */
export function atOrBefore(times: Float64Array, timeUs: number): number {
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (times[middle] <= timeUs) low = middle;
    else high = middle - 1;
  }
  return low;
}

/**
 * The frame a follower shows at `nowServerUs` (server µs): the intent's frame while paused, otherwise the frame due
 * after the time elapsed since `atUs`, at the intent's speed. Always inside the video.
 */
export function followFrame(times: Float64Array, intent: ClockIntent, nowServerUs: number): number {
  const anchor = Math.min(times.length - 1, Math.max(0, intent.frame));
  if (!intent.playing) return anchor;
  const elapsedUs = (nowServerUs - intent.atUs) * (intent.speedCenti / 100);
  return atOrBefore(times, times[anchor] + elapsedUs);
}

/** What the clock needs to know about the buffered frames. */
export interface FrameSource {
  has(sequenceId: number): boolean;
  /** The highest presentable sequence id in `notBefore..atOrBefore`. */
  newestPresentable(atOrBefore: number, notBefore: number): number | undefined;
}

/** The frame on screen; `periodUs` is the time to the next frame (to the last one from the previous, at the last frame), `null` for a one-frame video. */
export type FrameInfo = { sequenceId: number; timestampUs: number; periodUs: number | null };

export class PlaybackClock {
  readonly #times: Float64Array;
  readonly #now: () => number;
  readonly #last: number;
  readonly #failed = new Set<number>();
  #requested: number;
  #presented: number | undefined;
  /** Lowest sequence id the next tick may present: playing never goes backward. */
  #floor: number;
  #playing = false;
  #speed = 1;
  #anchorNow = 0;
  #anchorMedia = 0;

  /** `times` are presentation times in microseconds by sequence id; the clock starts paused on `startFrame`. */
  constructor(times: Float64Array, now: () => number, startFrame = 0) {
    this.#times = times;
    this.#now = now;
    this.#last = times.length - 1;
    this.#requested = this.#clamp(startFrame);
    this.#floor = this.#requested;
  }

  get playing(): boolean {
    return this.#playing;
  }

  get speed(): number {
    return this.#speed;
  }

  /** The time of the last frame in microseconds. */
  get durationUs(): number {
    return this.#times[this.#last];
  }

  /** The position the user asked for, or while playing the frame due now. This is the `Want` playhead. */
  get playhead(): number {
    return this.#requested;
  }

  /** The frame on screen, not the requested one. */
  info(): FrameInfo | undefined {
    if (this.#presented === undefined) return undefined;
    const at = this.#presented;
    const times = this.#times;
    const next = at + 1 < times.length ? times[at + 1] - times[at] : null;
    return {
      sequenceId: at,
      timestampUs: times[at],
      periodUs: next ?? (at > 0 ? times[at] - times[at - 1] : null),
    };
  }

  /** Marks a frame the backend could not decode: playing stops on it, as it does on the last frame. */
  fail(sequenceId: number): void {
    this.#failed.add(sequenceId);
  }

  /** Starts playing from the requested position; from the last frame it restarts at 0. */
  play(): void {
    if (this.#playing) return;
    if (this.#requested >= this.#last) this.#requested = 0;
    this.#floor = this.#requested;
    this.#playing = true;
    this.#anchor(this.#times[this.#requested]);
  }

  /** Stops on the frame on screen, so the pause time is never counted as lateness. */
  pause(): void {
    if (!this.#playing) return;
    this.#playing = false;
    this.#requested = this.#presented ?? this.#requested;
  }

  seek(sequenceId: number): void {
    this.#requested = this.#clamp(sequenceId);
    this.#floor = this.#requested;
    if (this.#playing) this.#anchor(this.#times[this.#requested]);
  }

  /** Steps relative to the requested position, not the frame on screen, which may lag behind it; playing goes on. */
  step(delta: number): void {
    this.seek(this.#requested + delta);
  }

  setSpeed(speed: number): void {
    if (this.#playing) this.#anchor(this.#mediaTime());
    this.#speed = clampSpeed(speed);
  }

  /**
   * Decides what to present now: at most one frame, the newest due one. Playing, a missing frame never slows the
   * clock: the newest buffered frame at or before the due time is shown. The last frame is always shown, then
   * playback finishes. A frame that failed to decode is never skipped: the frames before it are shown and the clock
   * stops on it.
   */
  tick(source: FrameSource): number | undefined {
    if (!this.#playing) {
      if (this.#presented === this.#requested || !source.has(this.#requested)) return undefined;
      this.#presented = this.#requested;
      return this.#presented;
    }
    const due = Math.min(this.#last, atOrBefore(this.#times, this.#mediaTime()));
    const failed = this.#failedUpTo(due);
    this.#requested = failed ?? due;
    if (failed !== undefined) this.#playing = false;
    const frame = source.newestPresentable(failed === undefined ? due : failed - 1, this.#floor);
    if (frame === undefined || frame === this.#presented) {
      // The last frame is already on screen, as when a one-frame video begins playing: the end is reached.
      if (due >= this.#last && this.#presented === this.#last) this.#playing = false;
      return undefined;
    }
    this.#presented = frame;
    this.#floor = frame + 1;
    if (frame >= this.#last) this.#playing = false;
    return frame;
  }

  /** The first failed frame the clock has not passed and that is due by `due`, if any. */
  #failedUpTo(due: number): number | undefined {
    for (let id = this.#floor; id <= due; id++) if (this.#failed.has(id)) return id;
    return undefined;
  }

  #anchor(mediaTimeUs: number): void {
    this.#anchorNow = this.#now();
    this.#anchorMedia = mediaTimeUs;
  }

  #mediaTime(): number {
    return this.#anchorMedia + (this.#now() - this.#anchorNow) * 1000 * this.#speed;
  }

  #clamp(sequenceId: number): number {
    return Math.min(this.#last, Math.max(0, Math.round(sequenceId)));
  }
}
