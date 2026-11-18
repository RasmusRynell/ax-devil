/** Shared shapes of the browser bench: what to run, what each run measured, and the machine it ran on. */

export type Mode = "play" | "paused" | "zoom";
export type Resolution = "small" | "1080p" | "4k";

/** One cell of the matrix: a viewer of `lanes` lanes with `objects` objects each, run for `seconds` at 30 fps. */
export interface Scenario {
  catalog: string;
  resolution: Resolution;
  lanes: number;
  objects: number;
  mode: Mode;
  seconds: number;
}

export const STAGES = [
  "filterEvaluate",
  "layoutQuads",
  "marks",
  "bufferUpload",
  "videoUpload",
  "atlasUpload",
  "draw",
  "present",
] as const;
export type Stage = (typeof STAGES)[number];

/** Milliseconds over the frames measured. */
export interface Stat {
  n: number;
  p50: number;
  p95: number;
  max: number;
}

export interface Result {
  scenario: Scenario;
  /** The step per stage; `present` is the whole step of unprobed frames, the GL stages come from probed frames. */
  stages: Record<Stage, Stat>;
  /** GPU time of the whole step from timer queries; null when the context does not expose them. */
  gpu: Stat | null;
  presented: number;
  /** Frames of the 30 fps cadence that were never shown. */
  dropped: number;
  droppedPercent: number;
  longTasks: { count: number; totalMs: number; maxMs: number };
  /** Intervals between animation frames longer than 1.5 periods of 30 fps. */
  rafGaps: { count: number; maxMs: number };
}

export interface Environment {
  glRenderer: string;
  glVendor: string;
  /** The renderer is a software rasterizer, so no number counts for the decision. */
  software: boolean;
  userAgent: string;
  cores: number;
  devicePixelRatio: number;
}

/** Settings from the environment, as text; unset ones take the defaults of `plan`. */
export type Settings = Partial<
  Record<"smoke" | "seconds" | "resolutions" | "catalogs" | "lanes" | "counts" | "modes", string>
>;

export interface BenchApi {
  environment(): Environment;
  plan(settings: Settings): Scenario[];
  run(scenario: Scenario): Promise<Result>;
  report(
    environment: Environment,
    results: Result[],
    notes: string[],
  ): { text: string; pass: boolean | null };
}
