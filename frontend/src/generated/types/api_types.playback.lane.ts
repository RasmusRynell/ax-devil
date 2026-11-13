// Generated from api_types::playback::lane by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { MatchBasis } from "./scene.time";

/**
 * How well an overlay lines up with the video, worst first.
 */
export type AlignmentVerdictWire = "weak" | "ends_far" | "partial" | "good";

/**
 * How an overlay's samples line up with the video's frames.
 */
export type AlignmentWire = { 
/**
 * What the samples were matched by.
 */
basis: MatchBasis, 
/**
 * How far back a sample matched a frame under time, in milliseconds; `null` for exact. The tolerance the counts
 * were computed with, which the lane's own may have moved on from.
 */
tolerance_ms: number | null, 
/**
 * How many samples the file has.
 */
samples: number, 
/**
 * How many of them a frame selects, exactly or within the tolerance.
 */
matched: number, 
/**
 * How many of them a frame selects exactly.
 */
exact: number, 
/**
 * By time, the last sample's time minus the last frame's time, in microseconds.
 */
end_gap_us: number | null, 
/**
 * Whether the last sample is more than a second and 5% of the video from the last frame.
 */
ends_far: boolean, 
/**
 * How well the file lines up, by its worst trait.
 */
verdict: AlignmentVerdictWire, };

/**
 * One class filter a lane offers.
 */
export type FilterOptionInfo = { 
/**
 * Stable id, such as `show_car`.
 */
id: string, 
/**
 * What the user reads.
 */
label: string, 
/**
 * Whether the filter starts switched on.
 */
default_enabled: boolean, };

/**
 * How far an overlay file has been read.
 */
export type IngestWire = { "state": "Reading" } | { "state": "Complete" } | { "state": "Incomplete" } & ErrorWire;

/**
 * What every lane starts with.
 */
export type LaneDefaultsWire = { 
/**
 * The catalog id, such as `built-in:standard`.
 */
catalog: string, 
/**
 * The sample selection every lane starts with.
 */
policy: LanePolicyWire, };

/**
 * One lane of the opened entry.
 */
export type LaneInfo = { 
/**
 * The lane's name.
 */
label: string, 
/**
 * The lane's identity within the entry, which the workspace keeps its matching under.
 */
slot: string, 
/**
 * The viewer's id of the source the lane draws; `null` for a plain lane. Lanes on the same file share one.
 */
source: number | null, 
/**
 * How the workspace says the lane matches its records to frames; absent is the defaults. The viewer starts the
 * lane from it.
 */
policy?: LanePolicyWire, };

/**
 * How a lane selects samples for frames: the part of a lane's settings the backend needs.
 */
export type LanePolicyWire = { 
/**
 * What records are matched to frames by; `null` is the decoder's default. A basis the file does not offer is
 * reported as a problem and the decoder's default is used.
 */
basis: MatchBasis | null, 
/**
 * How far back a sample may be under the time basis, in milliseconds; `null` is exact.
 */
tolerance_ms: number | null, 
/**
 * Whether old samples stay on screen.
 */
sticky: StickyWire, };

/**
 * Where a lane's load stands.
 */
export type LaneStatusWire = { "status": "Loading" } | { "status": "Ready", 
/**
 * The bases the file's records can be matched by.
 */
bases: Array<MatchBasis>, 
/**
 * The basis the decoder recommends, one of `bases`.
 */
default_basis: MatchBasis, 
/**
 * The class filters the file's content calls for.
 */
filter: Array<FilterOptionInfo>, 
/**
 * Problems the decoder reported, and a misalignment with the video's frames.
 */
warnings: Array<WarningInfo>, 
/**
 * How the samples line up with the video's frames under the lane's basis and tolerance; `null` while the
 * file is read. Sent again when a change of either alters it.
 */
alignment: AlignmentWire | null, 
/**
 * How many samples the file has.
 */
samples: number, 
/**
 * The source's revision; its Sample Items and the lane's Frame Maps carry it.
 */
data_revision: number, 
/**
 * The mean size of a Sample Item in bytes, 0 for an empty file; sizes the viewer's data window.
 */
mean_sample_bytes: number, 
/**
 * How far the file has been read. A file still being read repeats this status under the same
 * `data_revision` as more of it arrives, and under a new one when the numbering of its samples changes;
 * the samples and Frame Maps held stay valid for an equal revision.
 */
ingest: IngestWire, } | { "status": "Failed" } & ErrorWire;

/**
 * Keeping the last sample on screen while no sample matches.
 */
export type StickyWire = { 
/**
 * Whether samples are kept.
 */
enabled: boolean, 
/**
 * How old a kept sample may be, in milliseconds, at most 100000; `null` is without limit.
 */
timeout_ms: number | null, };

/**
 * A problem that did not stop an overlay from loading.
 */
export type WarningInfo = { 
/**
 * What happened.
 */
problem: string, 
/**
 * The first reasons.
 */
reasons: Array<string>, 
/**
 * What the user can expect as a result.
 */
consequence: string, 
/**
 * Whether more reasons exist than are listed.
 */
more_reasons: boolean, };
