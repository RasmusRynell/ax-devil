import type { Mode, Resolution, Scenario, Settings } from "./types";

const list = (text: string | undefined, fallback: string): string[] =>
  (text || fallback).split(",").map((item) => item.trim());

/**
 * The scenarios to run. By default the whole matrix: both frame sizes, the three built-in catalogs, 4 lanes of 1000
 * and of 5000 objects, in the three modes, 20 seconds (600 frames) each. `smoke` is a few seconds in all.
 */
export function plan(settings: Settings): Scenario[] {
  const smoke = Boolean(settings.smoke);
  const fallback = smoke
    ? { resolutions: "small", catalogs: "standard", lanes: "1", counts: "100", seconds: "1" }
    : {
        resolutions: "1080p,4k",
        catalogs: "standard,classic,glass",
        lanes: "4",
        counts: "1000,5000",
        seconds: "20",
      };
  const seconds = Number(settings.seconds || fallback.seconds);
  const lanes = Number(settings.lanes || fallback.lanes);
  const scenarios: Scenario[] = [];
  for (const resolution of list(settings.resolutions, fallback.resolutions) as Resolution[])
    for (const catalog of list(settings.catalogs, fallback.catalogs))
      for (const objects of list(settings.counts, fallback.counts).map(Number))
        for (const mode of list(settings.modes, "play,paused,zoom") as Mode[])
          scenarios.push({ catalog, resolution, lanes, objects, mode, seconds });
  return scenarios;
}
