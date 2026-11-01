import type { LaneStatusWire } from "../generated/api";
import type { ClassMask, CompiledCatalog, CompiledFilter, SampleView } from "../present";
import type { VideoFrame } from "./protocol";

/**
 * What the session hands the display for one lane of one frame: the held Sample Item and everything the presenter
 * needs to turn it into quads. The session owns the values; the presenter caches by the revisions inside.
 */
export interface LaneFrame {
  /** The viewer's source id of the lane's file, with the revision the held sample belongs to. */
  readonly source: number;
  readonly dataRevision: number;
  /** The sample's index in its source. */
  readonly sample: number;
  readonly columns: SampleView;
  readonly catalog: CompiledCatalog;
  /** Changes whenever the catalog choice, class toggles, hidden features or theme change what is drawn. */
  readonly styleRevision: number;
  readonly classMask: ClassMask;
  readonly filter: CompiledFilter | null;
  /** Bits of the catalog's hidden features. */
  readonly hidden: number;
  readonly theme: string | null;
  /** The painter's opacity for the lane's marks, 0 to 1. */
  readonly opacity: number;
}

/** Paints a decoded video frame with one lane frame per lane (`undefined`: nothing to draw for that lane). */
export interface Surface {
  present(frame: VideoFrame, lanes: readonly (LaneFrame | undefined)[]): void;
}

/** The settings of one lane. `tolerance_ms` and `sticky` (except `opacity`) and `visible` go to the backend. */
export type LaneSettings = {
  /** The catalog id, such as `built-in:standard`. */
  catalog: string;
  /** Class toggles by option id; options not listed keep their default. */
  filter: Record<string, boolean>;
  tolerance_ms: number | null;
  sticky: { enabled: boolean; timeout_ms: number | null; opacity: number };
  visible: boolean;
};

/** What the UI shows of one lane. */
export type LaneState = {
  label: string;
  /** The viewer's source id; `null` for a plain lane. */
  source: number | null;
  status: LaneStatusWire | "plain";
  settings: LaneSettings;
  /** Counts the changes sent to the backend; Frame Maps carry the revision they were selected under. */
  policyRevision: number;
  /** The last problem the backend reported for the lane. */
  problem?: string;
  /** Why the lane's catalog is not the chosen one. */
  catalogProblem?: string;
  /** Whether the lane waits for data of the shown frame. */
  pending: boolean;
};

/** How the UI changes a lane; `PlaybackSession` implements it (replacing `setLaneView` and `setLaneTargets`). */
export interface LaneControl {
  setLane(lane: number, change: Partial<LaneSettings>): void;
}
