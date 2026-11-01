/**
 * The frontend presentation clock. The frontend owns the clock and the backend only sees its position as `Want`s.
 * Time comes from the injected `now()` (milliseconds); the caller calls `tick` on every display refresh.
 */

export const MIN_SPEED = 0.01;
export const MAX_SPEED = 10;
export const SPEED_STEP = 0.1;

/** Speed rounded to hundredths and clamped to 0.01..10. */
export function clampSpeed(speed: number): number {
  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(speed * 100) / 100));
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

/** What a presenter says the Session's clock is doing; `atUs` is the presenter's stamp in server time (µs). */
export interface ClockIntent {
  frame: number;
  playing: boolean;
  /** Speed in hundredths: 100 is real time. */
  speedCenti: number;
  atUs: number;
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

export type FrameInfo = { sequenceId: number; timestampUs: number };

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

  /** The position the user asked for, or while playing the frame due now. This is the `Want` playhead. */
  get playhead(): number {
    return this.#requested;
  }

  /** The frame on screen, not the requested one. */
  info(): FrameInfo | undefined {
    if (this.#presented === undefined) return undefined;
    return { sequenceId: this.#presented, timestampUs: this.#times[this.#presented] };
  }

  /**
   * Marks a frame the backend could not decode. It is never presented and the clock never waits for it; playback
   * ends on the last frame that did not fail.
   */
  skip(sequenceId: number): void {
    this.#failed.add(sequenceId);
  }

  /** Starts playing from the requested position; from the last frame it restarts at 0. */
  play(): void {
    if (this.#playing) return;
    if (this.#requested >= this.#end()) this.#requested = 0;
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

  /** Steps relative to the requested position, not the frame on screen, which may lag behind it; pauses. */
  step(delta: number): void {
    const from = this.#requested;
    this.pause();
    this.seek(from + delta);
  }

  setSpeed(speed: number): void {
    if (this.#playing) this.#anchor(this.#mediaTime());
    this.#speed = clampSpeed(speed);
  }

  /** One 0.1 step up (`1`) or down (`-1`). */
  stepSpeed(direction: 1 | -1): void {
    this.setSpeed(this.#speed + direction * SPEED_STEP);
  }

  /**
   * Decides what to present now: at most one frame, the newest due one. Playing, a missing frame never slows the
   * clock: the newest buffered frame at or before the due time is shown. The last frame is always shown, then
   * playback finishes.
   */
  tick(source: FrameSource): number | undefined {
    if (!this.#playing) {
      if (this.#presented === this.#requested || !source.has(this.#requested)) return undefined;
      this.#presented = this.#requested;
      return this.#presented;
    }
    const due = Math.min(this.#last, atOrBefore(this.#times, this.#mediaTime()));
    this.#requested = due;
    const frame = source.newestPresentable(due, this.#floor);
    if (frame === undefined || frame === this.#presented) {
      // Waiting for the end frame is over once it turns out to have failed.
      if (due >= this.#end() && this.#presented !== undefined && this.#presented >= this.#end())
        this.#playing = false;
      return undefined;
    }
    this.#presented = frame;
    this.#floor = frame + 1;
    if (frame >= this.#end()) this.#playing = false;
    return frame;
  }

  /** The frame playback ends on: the last one that did not fail. */
  #end(): number {
    let end = this.#last;
    while (end > 0 && this.#failed.has(end)) end--;
    return end;
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
