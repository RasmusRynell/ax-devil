/** The rows of the info HUD: how a frame and the overlay sample drawn on it line up in time, in whole microseconds. */

import type { FrameInfo } from "../stream/clock";
import type { LaneSettings } from "../lanes/contract";
import type { LaneMatch } from "../lanes/laneData";
import {
  MATCH_EXACT,
  MATCH_TOLERATED,
  type BasisWire,
  type VideoPtsWire,
} from "../stream/protocol";

/** What the HUD reads of one lane: how its sample was matched to the frame, whether it still waits, and how it matches. */
export type HudLane = {
  match: LaneMatch | null;
  pending: boolean;
  settings: Pick<LaneSettings, "tolerance_ms">;
  /** The basis the lane matches by, after the file's default. */
  basis: BasisWire;
};

/** A titled block of `[label, value]` rows. */
export type HudSection = { title: string; rows: readonly (readonly [string, string])[] };

/**
 * The PTS of the frame at `timestampUs`, from the frame's time `(pts - first_pts) * time_base`. A time base finer than a
 * microsecond cannot be told from the frame times, so it has none (`null`).
 */
export function ptsOf(pts: VideoPtsWire, timestampUs: number): number | null {
  const [num, den] = pts.time_base;
  if (num * 1e6 < den) return null;
  return pts.first_pts + Math.round((timestampUs * den) / (num * 1e6));
}

const text = (value: number | null): string => (value === null ? "None" : String(value));

function status(frame: FrameInfo, lane: HudLane | null, matched: LaneMatch | null): string {
  if (matched === null) return lane?.pending ? "WAITING for overlay data" : "NO OVERLAY";
  if (matched.timeUs === null) return "UNKNOWN overlay timestamp missing";
  const difference = frame.timestampUs - matched.timeUs;
  if (difference < 0) return `VIOLATION overlay is ${-difference} after frame`;
  return difference === 0 ? "OK exact timestamp match" : "OK overlay is before frame";
}

function frameRows(frame: FrameInfo, pts: VideoPtsWire | null): [string, string][] {
  const rows: [string, string][] = [
    ["Frame id", String(frame.sequenceId)],
    ["Frame timestamp", String(frame.timestampUs)],
  ];
  if (pts === null) return rows;
  const at = ptsOf(pts, frame.timestampUs);
  rows.push(["Timestamp source", "pts_time_base_minus_first_pts"]);
  if (at !== null) rows.push(["PTS", String(at)]);
  rows.push(["First PTS", String(pts.first_pts)]);
  if (at !== null) rows.push(["PTS delta", String(at - pts.first_pts)]);
  rows.push(["Time base", `${pts.time_base[0]}/${pts.time_base[1]}`]);
  if (frame.periodUs !== null) rows.push(["Period after", String(frame.periodUs)]);
  rows.push(["Period source", "pts_delta"]);
  return rows;
}

const KINDS = new Map([
  [MATCH_EXACT, "exact"],
  [MATCH_TOLERATED, "tolerated"],
]);

function matchRows(frame: FrameInfo, lane: HudLane, matched: LaneMatch): [string, string][] {
  const { tolerance_ms } = lane.settings;
  const byFrame = lane.basis === "Frame";
  const toleranceUs = tolerance_ms === null ? null : tolerance_ms * 1000;
  const rows: [string, string][] = [
    ["Requested timestamp", String(frame.timestampUs)],
    ["Matched timestamp", text(matched.timeUs)],
    ["Match type", KINDS.get(matched.kind) ?? "retained"],
    ["Tolerance", text(toleranceUs)],
    ["Effective tolerance", String(byFrame ? 0 : (toleranceUs ?? 0))],
    ["Fallback mode", tolerance_ms === null ? "exact_only" : "previous_with_tolerance"],
    ["Alignment basis", byFrame ? "sequence" : "timestamp"],
    ["Requested sequence", String(frame.sequenceId)],
    ["Matched sequence", text(matched.frame)],
  ];
  if (matched.timeUs !== null)
    rows.push(["Lookup offset", String(matched.timeUs - frame.timestampUs)]);
  return rows;
}

/**
 * The HUD's sections for the frame on screen: Timing, Frame (with the PTS rows when the video has `pts`) and Overlay
 * for `lane` (`null`: a lane without a file). An overlay is there when the lane matched a sample to the frame.
 */
export function hudSections(
  frame: FrameInfo,
  pts: VideoPtsWire | null,
  lane: HudLane | null,
): HudSection[] {
  const hit = lane?.match ?? null;
  const matched = hit !== null && hit.sample !== null ? hit : null;
  const difference = matched?.timeUs == null ? null : frame.timestampUs - matched.timeUs;
  return [
    {
      title: "Timing",
      rows: [
        ["Unit", "microseconds"],
        ["Status", status(frame, lane, matched)],
        ["Frame - overlay", text(difference)],
      ],
    },
    { title: "Frame", rows: frameRows(frame, pts) },
    {
      title: "Overlay",
      rows: [
        ["Overlay id", text(matched?.frame ?? null)],
        ["Overlay timestamp", text(matched?.timeUs ?? null)],
        ...(lane && matched ? matchRows(frame, lane, matched) : []),
      ],
    },
  ];
}

/**
 * How many of the HUD's lines (headings and rows, in order) fit above `limit`: the leading run whose `bottom` does, less
 * a heading left with no row under it.
 */
export function linesFit(
  lines: readonly { heading: boolean; bottom: number }[],
  limit: number,
): number {
  let count = lines.findIndex((line) => line.bottom > limit);
  if (count < 0) return lines.length;
  if (count > 0 && lines[count - 1].heading) count -= 1;
  return count;
}
