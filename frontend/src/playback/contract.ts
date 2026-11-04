import type { BasisWire, FieldWire, LaneStatusWire } from "../generated/api";
import type { CellRect } from "../overlay/painter";
import type {
  ClassMask,
  CompiledCatalog,
  CompiledFilter,
  MeasureRanges,
  SampleView,
} from "../present";
import type { LaneMatch } from "./laneData";
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

/**
 * The settings of one lane. `basis`, `tolerance_ms`, `sticky` (except `opacity`) and `visible` go to the backend; the rest is
 * how the client draws what it holds.
 */
export type LaneSettings = {
  /** The catalog id, such as `built-in:standard` or `user:night.json`. */
  catalog: string;
  /** One of the catalog's themes; `null` is the catalog's own look. */
  theme: string | null;
  /** Bits of the catalog's features switched off (`FEATURES[i]` is bit i). */
  hidden: number;
  /** Class toggles by option id; options not listed keep their default. */
  filter: Record<string, boolean>;
  /** The frame filter expression that is applied; `""` is none. Only an expression that compiles is stored. */
  expression: string;
  /** Inclusive score and confidence ranges, `null` for an open end; a row without the value is kept. */
  ranges: MeasureRanges;
  /** What the lane matches the file's records to frames by; `null` is the decoder's default, as is a basis the file lacks. */
  basis: BasisWire | null;
  tolerance_ms: number | null;
  sticky: { enabled: boolean; timeout_ms: number | null; opacity: number };
  /** The timeout the lane last had, which unchecking the sticky expiry keeps and checking it again restores. Local to the client. */
  kept_timeout_ms: number;
  visible: boolean;
};

/** How a lane keeps old samples on screen; also what new lanes start with, as the Personal setting holds it. */
export type Sticky = LaneSettings["sticky"];

/** What the catalog a lane draws with offers its menu. */
export type CatalogLook = {
  /** The catalog's theme names. */
  readonly themes: readonly string[];
  /** Bit i set when the catalog has a layer for `FEATURES[i]`. */
  readonly features: number;
};

/** What the UI shows of one lane. */
export type LaneState = {
  label: string;
  /** The lane's identity within its entry, which the workspace keeps its matching under. */
  slot: string;
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
  /** What the catalog the lane draws with offers; absent while the lane has none. */
  look?: CatalogLook;
  /** The revision of the document the lane's chosen catalog last compiled from; absent until one did. */
  catalogRevision?: number;
  /** Whether the lane waits for data of the shown frame. */
  pending: boolean;
};

/** How the UI changes a lane; `PlaybackSession` implements it (replacing `setLaneView` and `setLaneTargets`). */
export interface LaneControl {
  setLane(lane: number, change: Partial<LaneSettings>): void;
}

/** What the lane grid and its canvas ask of a viewer's session: a playback session and a live session both implement it. */
export interface LaneDisplay extends LaneControl {
  /** Gives every other lane of the viewer the settings of `lane`, but its visibility. */
  applyToAll(lane: number): void;
  /** How `lane` matched a sample to the frame on screen. */
  matched(lane: number): LaneMatch | null;
  /** Sets where frames are drawn; `null` stops drawing. */
  setSurface(surface: Surface | null): void;
  /** Draws the shown frame again, for a surface that lost its picture. */
  represent(): void;
}

/** How the viewer's pictures are zoomed: one for all of a viewer's lanes. Pan is a fraction of the zoomed picture. */
export type Viewport = { readonly zoom: number; readonly panX: number; readonly panY: number };

/** The object the user clicked: its entity id in the lane's source. `null` is nothing selected. */
export type Selection = {
  readonly lane: number;
  readonly source: number;
  readonly entity: string;
} | null;

/** The object under a pointer, found in the lane's kept rows. */
export type Picked = {
  lane: number;
  source: number;
  sample: number;
  /** The row in the sample. */
  row: number;
  entity: string;
  /** The chip's first line, such as `7 car`. */
  label: string;
  /** The chip's second line, such as `score 0.82 · confidence 0.9`. */
  detail: string;
  /** Which of the chip's category colours marks the object's class, from 0. */
  category: number;
  /** The object's box in CSS pixels of the canvas. */
  box: CellRect;
};

/** Whether an object moves, as the Sample Item says. */
export type Motion = "moving" | "stationary" | "unknown";

/** What the held Sample Item says of the selected object, which is shown before the backend answers. */
export type Summary = {
  entity: string;
  className: string | null;
  score: number | null;
  confidence: number | null;
  /** `null` when the item gives the object no motion. */
  motion: Motion | null;
};

/** What the inspector shows of the selection. */
export type InspectionView =
  | { state: "none" }
  /**
   * The shown sample shows no such object; the selection stays. `gone`: the sample has none; `filtered`: it has one that
   * the lane's class toggles or frame filter hide. `last` is the object's summary when it was last shown.
   */
  | {
      state: "absent";
      selection: NonNullable<Selection>;
      reason: "gone" | "filtered";
      last: Summary | null;
    }
  | {
      /** `loading`: no reply for the shown sample yet; `fields` is the last reply's. */
      state: "loading" | "ready" | "unavailable";
      selection: NonNullable<Selection>;
      summary: Summary;
      fields: readonly FieldWire[];
      truncated: boolean;
    };
