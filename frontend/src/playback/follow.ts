import { clampSpeed, followFrame, type ClockIntent, type PlaybackClock } from "./clock";

type Handed = { intent: ClockIntent; serverNowUs: () => number };

/**
 * The clock another viewer left this one: a presenter's, followed until the viewer acts, or an owner's, taken over
 * once when the video opens, after which the viewer is the owner and publishes its own pauses.
 */
export class ClockHandoff {
  #followed: Handed | undefined;
  #restored: Handed | undefined;

  /** Whether a presenter's clock is followed, so the viewer publishes nothing of its own. */
  get following(): boolean {
    return this.#followed !== undefined;
  }

  /** Whether an owner's clock waits for the video to open. */
  get pending(): boolean {
    return this.#restored !== undefined;
  }

  follow(intent: ClockIntent, serverNowUs: () => number): void {
    this.#followed = { intent, serverNowUs };
  }

  restore(intent: ClockIntent, serverNowUs: () => number): void {
    this.#restored = { intent, serverNowUs };
  }

  /** The viewer acted: it follows no more. */
  unfollow(): void {
    this.#followed = undefined;
  }

  /** Drops an owner's clock that was not taken over. */
  forget(): void {
    this.#restored = undefined;
  }

  /** Puts `clock` where the clock handed over is by now; an owner's is taken over once. Undefined when none is. */
  apply(
    clock: PlaybackClock,
    times: Float64Array,
  ): { speed: number; started: boolean } | undefined {
    const taken = this.#followed ?? this.#restored;
    if (!taken) return undefined;
    this.#restored = undefined;
    return followClock(clock, times, taken.intent, taken.serverNowUs());
  }
}

/**
 * Puts `clock` where the presenter's is at `serverNowUs` (the backend's time) and at the presenter's speed. Returns that
 * speed, and whether the clock started playing, which the want planner must hear.
 */
export function followClock(
  clock: PlaybackClock,
  times: Float64Array,
  intent: ClockIntent,
  serverNowUs: number,
): { speed: number; started: boolean } {
  const frame = followFrame(times, intent, serverNowUs);
  const speed = clampSpeed(intent.speedCenti / 100);
  clock.setSpeed(speed);
  if (intent.playing && frame < times.length - 1) {
    clock.seek(frame);
    const started = !clock.playing;
    if (started) clock.play();
    return { speed, started };
  }
  clock.pause();
  clock.seek(frame);
  return { speed, started: false };
}
