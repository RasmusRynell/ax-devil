import { clampSpeed, followFrame, type ClockIntent, type PlaybackClock } from "./clock";

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
