/** The bench's printed output: the header, the table, and the decision rule applied to the results. */

import type { Environment, Result, Stat } from "./types";

/** The owner's rule for staying on the main thread, at 4 lanes of 1000 objects while playing. */
export const RULE = { presentP95: 8, marksP95: 4, droppedPercent: 1 };

export function header(environment: Environment): string {
  return [
    "ax-devil browser bench: the real VideoRenderer in Electron, synthetic I420 frames, no decode.",
    `GL renderer: ${environment.glRenderer} (${environment.glVendor})${environment.software ? "  SOFTWARE" : ""}`,
    `${environment.cores} cores, devicePixelRatio ${environment.devicePixelRatio}`,
    "",
    "Only a run on a real GPU counts for the decision (a SwiftShader run proves the bench works, not the speed).",
    `Rule: stay on the main thread if, at 4 lanes x 1000 objects while playing, the whole present step is`,
    `<= ${RULE.presentP95} ms p95, filter+evaluate+layout+quad write is <= ${RULE.marksP95} ms p95 and dropped frames are <= ${RULE.droppedPercent}%.`,
    "Otherwise add mark reuse and measure again; if still over, a worker per viewer is a candidate for the owner.",
    "",
    "Times are ms per step over all lanes, p50/p95/max. present: the whole step (renderer.present or, in zoom mode,",
    "zoomAt+setViewport) on frames without GL probes. marks: filter+evaluate+layout+quad write. The GL stages come",
    "from every 4th frame, which times each GL call. Modes: play (new sample every frame), paused (cache hits),",
    "zoom (wheel zoom every frame on a paused sample). drop%: frames of the 30 fps cadence never shown.",
  ].join("\n");
}

const f = (value: number): string => value.toFixed(2);
const trio = ({ p50, p95, max }: Stat): string => `${f(p50)}/${f(p95)}/${f(max)}`;
const duo = ({ p50, p95 }: Stat): string => `${f(p50)}/${f(p95)}`;

const COLUMNS = [
  "catalog   res   lanes x obj mode   ",
  "present p50/p95/max   ",
  "marks p50/p95      ",
  "filt+eval         ",
  "layout+quads      ",
  "bufUp p50/p95 ",
  "vidUp p50/p95 ",
  "atlUp p50/p95 ",
  "draw p50/p95  ",
  "gpu p50/p95   ",
  "long n/ms   ",
  "gaps ",
  "drop%",
];

function row({ scenario: s, stages, gpu, longTasks, rafGaps, droppedPercent }: Result): string {
  return [
    `${s.catalog.padEnd(9)} ${s.resolution.padEnd(5)} ${`${s.lanes} x ${s.objects}`.padEnd(11)} ${s.mode.padEnd(7)}`,
    trio(stages.present).padEnd(22),
    duo(stages.marks).padEnd(18),
    duo(stages.filterEvaluate).padEnd(17),
    duo(stages.layoutQuads).padEnd(17),
    duo(stages.bufferUpload).padEnd(14),
    duo(stages.videoUpload).padEnd(14),
    duo(stages.atlasUpload).padEnd(14),
    duo(stages.draw).padEnd(14),
    (gpu ? duo(gpu) : "n/a").padEnd(14),
    `${longTasks.count}/${longTasks.totalMs.toFixed(0)}`.padEnd(12),
    String(rafGaps.count).padEnd(5),
    droppedPercent.toFixed(1),
  ].join(" ");
}

/** Whether one result passes the rule; null for a result the rule does not cover. */
export function passes(result: Result): boolean | null {
  const { scenario: s, stages, droppedPercent } = result;
  if (s.lanes !== 4 || s.objects !== 1000 || s.mode !== "play") return null;
  return (
    stages.present.p95 <= RULE.presentP95 &&
    stages.marks.p95 <= RULE.marksP95 &&
    droppedPercent <= RULE.droppedPercent
  );
}

/** The table, then PASS or FAIL per covered result and overall. `pass` is null when nothing is covered. */
export function report(
  environment: Environment,
  results: Result[],
  notes: string[],
): { text: string; pass: boolean | null } {
  const judged = results
    .map((result) => ({ result, pass: passes(result) }))
    .filter((r) => r.pass !== null);
  const pass = judged.length === 0 ? null : judged.every((r) => r.pass);
  const grade = environment.software ? " (software renderer: not decision-grade)" : "";
  const lines = [
    COLUMNS.join(" "),
    ...results.map(row),
    "",
    ...notes,
    ...judged.map(
      ({ result: { scenario: s }, pass: ok }) =>
        `  ${ok ? "PASS" : "FAIL"}  ${s.catalog} ${s.resolution} 4 x 1000 play${grade}`,
    ),
    `RULE: ${pass === null ? "not covered (needs 4 lanes x 1000 objects in play mode)" : pass ? "PASS" : "FAIL"}${pass === null ? "" : grade}`,
  ];
  return { text: lines.join("\n"), pass };
}
