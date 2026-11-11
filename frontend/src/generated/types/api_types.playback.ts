// Generated from api_types::playback by make; do not edit.

import type { CatalogInfoWire } from "./api_types.catalogs";
import type { ErrorParams, ErrorWire } from "./api_types.error";
import type { InspectionWire, ObjectItemsWire } from "./api_types.inspect";
import type { LiveAction, LiveEventsWire, LiveGapWire, LiveOpenedWire, LiveStatusWire } from "./api_types.live";
import type { EventAskWire, EventQueryWire } from "./api_types.playback.events";
import type { ExportFormatWire, ExportLaneWire, ExportStateWire } from "./api_types.playback.export";
import type { FileAnswer } from "./api_types.playback.file_scope";
import type { LaneDefaultsWire, LaneInfo, LanePolicyWire, LaneStatusWire } from "./api_types.playback.lane";
import type { CadenceWire, VideoPtsWire, Want } from "./api_types.playback.want";
import type { ErrorCode } from "./foundation.error";
import type { FileSortWire, SearchWire } from "./scene_store.query";

/**
 * What the frontend sends.
 */
export type ClientMessage = { "type": "Open", 
/**
 * The ticket.
 */
ticket: string, 
/**
 * The frame to show, paused; the first frame when absent.
 */
start_frame?: number, 
/**
 * The most bytes of Sample Items the viewer holds at once, fixed for the stream. The backend never has more
 * than this sent, except one item larger than it alone.
 */
data_bytes: number, } | { "type": "Want" } & Want | { "type": "LanePolicy", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Counts the lane's changes; Frame Maps carry the revision they were selected under.
 */
revision: number, 
/**
 * How samples are selected.
 */
policy: LanePolicyWire, 
/**
 * Whether the lane is shown; an unsubscribed lane gets no Frame Maps and its samples are not sent for it.
 */
subscribed: boolean, } | { "type": "Prepare", 
/**
 * The entry's id, `EntryInfo::id`.
 */
entry: string | null, 
/**
 * The labels of the lanes to prepare, `EntryInfo::lanes`.
 */
lanes: Array<string>, } | { "type": "Catalog", 
/**
 * The catalog id, such as `built-in:standard`.
 */
id: string, } | { "type": "Inspect", 
/**
 * The viewer's source id, `LaneInfo::source`.
 */
source: number, 
/**
 * The source revision the sample belongs to.
 */
data_revision: number, 
/**
 * The sample's index in its source.
 */
sample: number, 
/**
 * The object's id; at most 1024 bytes.
 */
entity: string, } | { "type": "InspectFields", 
/**
 * The viewer's source id, `LaneInfo::source`.
 */
source: number, 
/**
 * The source revision the sample belongs to.
 */
data_revision: number, 
/**
 * The sample's index in its source.
 */
sample: number, 
/**
 * The paths a search names, in its spelling; at most 64, each at most 1024 bytes.
 */
paths: Array<string>, } | { "type": "Export", 
/**
 * Names the job in its `Export` messages.
 */
job: number, 
/**
 * The lanes to draw, in the order they tile.
 */
lanes: Array<ExportLaneWire>, 
/**
 * The first frame, a sequence id.
 */
first: number, 
/**
 * The last frame, inclusive; equal to `first` for a PNG.
 */
last: number, 
/**
 * What to write.
 */
format: ExportFormatWire, 
/**
 * Where to write it on the backend's machine: an absolute path.
 */
path: string, } | { "type": "CancelExport", 
/**
 * The job to cancel.
 */
job: number, } | { "type": "FileQuery", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Names the request in its answer; the client drops answers of older requests.
 */
request: number, 
/**
 * The search, parsed by the client.
 */
query: SearchWire, 
/**
 * The lane's class toggles: bit `i % 32` of word `i / 32` is set for each enabled filter option, as in the
 * Sample Item's class mask.
 */
classes: Array<number>, 
/**
 * The order of the rows.
 */
sort: FileSortWire, 
/**
 * The first row to send.
 */
offset: number, 
/**
 * How many rows to send at most, from 1 to 200.
 */
limit: number, 
/**
 * The most spans of presence per row, from 0 to 256; 0 sends none.
 */
bins: number, 
/**
 * An object whose row in the list is wanted whatever the page: the answer's `located`.
 */
locate: string | null, } | { "type": "FileRelease", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, } | { "type": "Live", 
/**
 * What to do.
 */
action: LiveAction, } | { "type": "Appearances", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Names the request in its answer; the client drops answers of older requests.
 */
request: number, 
/**
 * The object's id.
 */
entity: string, } | { "type": "EventQuery" } & EventQueryWire | { "type": "Event" } & EventAskWire | { "type": "Close" };

/**
 * What the backend sends as text. `Opened` is followed by a Timeline envelope.
 */
export type ServerMessage = { "type": "Indexing", 
/**
 * How far along it is, from 0 to 1.
 */
progress: number, } | { "type": "Opened", 
/**
 * The protocol version, to compare with `PLAYBACK_PROTOCOL`.
 */
protocol: number, 
/**
 * How many frames the video has.
 */
frame_count: number, 
/**
 * Video length in microseconds.
 */
duration_us: number, 
/**
 * Bytes one frame takes once the viewer holds it: the sum of its plane lengths (planes are tightly packed),
 * or width x height x 4 of the delivered size when frames arrive as JPEG and are decoded to RGBA.
 */
frame_bytes: number, 
/**
 * Width of the decoded frames in pixels.
 */
width: number, 
/**
 * Height of the decoded frames in pixels.
 */
height: number, 
/**
 * Frames per second the video's stream declares; `null` when it declares none.
 */
fps: number | null, 
/**
 * Problems with the video that did not stop it from opening. Only for an audience with host details.
 */
warnings: Array<string>, 
/**
 * The lanes of the entry, in order.
 */
lanes: Array<LaneInfo>, 
/**
 * What every lane starts with.
 */
lane_defaults: LaneDefaultsWire, 
/**
 * The catalogs a lane may draw with, built-ins first and Standard first among them.
 */
catalogs: Array<CatalogInfoWire>, 
/**
 * The video's PTS time base and first PTS; `null` for an image sequence.
 */
video_pts: VideoPtsWire | null, 
/**
 * How evenly the video's frames are spaced; `null` for a one-frame video.
 */
cadence: CadenceWire | null, } | { "type": "LaneStatus", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Where the load stands.
 */
status: LaneStatusWire, } | { "type": "LaneProblem", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * What went wrong, in words for the user.
 */
reason: string, } | { "type": "Catalog", 
/**
 * The requested id.
 */
id: string, 
/**
 * Counts document changes; 0 when unavailable to this viewer. Availability is ordered by the feed, not this number.
 */
revision: number, 
/**
 * The catalog document, or `null` when no such catalog exists or it cannot be served.
 */
document: string | null, 
/**
 * Why an existing catalog cannot be served (too large, unreadable, not text); `null` otherwise.
 */
problem: string | null, } | { "type": "Catalogs", 
/**
 * Built-ins first and Standard first among them, then the user's.
 */
catalogs: Array<CatalogInfoWire>, } | { "type": "Inspected", 
/**
 * The requested source.
 */
source: number, 
/**
 * The requested revision.
 */
data_revision: number, 
/**
 * The requested sample.
 */
sample: number, 
/**
 * The requested object.
 */
entity: string, 
/**
 * What the backend knows of it.
 */
inspection: InspectionWire, } | { "type": "InspectedFields", 
/**
 * The requested source.
 */
source: number, 
/**
 * The requested revision.
 */
data_revision: number, 
/**
 * The requested sample.
 */
sample: number, 
/**
 * The requested paths.
 */
paths: Array<string>, 
/**
 * The objects that have a leaf at the paths; `null` when the viewer does not hold this sample under this
 * revision, its authority no longer reaches it, or the search is more than the backend will do over it.
 */
objects: Array<ObjectItemsWire> | null, } | { "type": "FrameError", 
/**
 * The frame that failed.
 */
sequence_id: number, 
/**
 * The public kind.
 */
code: ErrorCode, 
/**
 * The values the code's message needs.
 */
params: ErrorParams, 
/**
 * What went wrong, in words for the user. Only for an audience with host details.
 */
reason?: string, 
/**
 * What it is about, such as the file path or the settings field key. Only for an audience with host details,
 * and only when it is about something.
 */
location?: string, } | { "type": "DataWindow", 
/**
 * The first frame.
 */
first: number, 
/**
 * The last frame.
 */
last: number, } | { "type": "Cached", 
/**
 * Sorted, disjoint, non-adjacent inclusive runs `[first, last]` of sequence ids.
 */
ranges: Array<[number, number]>, } | { "type": "Evict", 
/**
 * The viewer's source id, `LaneInfo::source`.
 */
source: number, 
/**
 * The source revision the samples belong to.
 */
data_revision: number, 
/**
 * The sample indices, ascending and distinct.
 */
samples: Array<number>, } | { "type": "Export", 
/**
 * The job from the request.
 */
job: number, 
/**
 * Where it stands.
 */
state: ExportStateWire, } | { "type": "File", 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * The request answered.
 */
request: number, 
/**
 * What the file holds, or why it cannot be asked.
 */
answer: FileAnswer, } | { "type": "LiveOpened" } & LiveOpenedWire | { "type": "LiveStatus" } & LiveStatusWire | { "type": "LiveEvents" } & LiveEventsWire | { "type": "LiveGap" } & LiveGapWire | { "type": "Error" } & ErrorWire;

/**
 * Raised when the messages or the envelope change in a way an older frontend cannot read, or a message is added
 * that an older backend would reject.
 */
export const PLAYBACK_PROTOCOL = 12;

/**
 * "AXDV" read as a little-endian u32.
 */
export const ENVELOPE_MAGIC = 1447319617;

/**
 * The envelope format version, byte 4.
 */
export const ENVELOPE_VERSION = 1;

/**
 * Bytes of the fixed envelope head.
 */
export const ENVELOPE_HEAD_BYTES = 28;

/**
 * Where the times of a Timeline start: the head padded to a multiple of 8 for the `f64` values.
 */
export const TIMELINE_HEADER_BYTES = 32;

/**
 * Where the planes of a VideoFrame start, at least; the head plus the video header.
 */
export const VIDEO_FRAME_HEADER_BYTES = 64;

/**
 * Envelope kind: one decoded video frame.
 */
export const KIND_VIDEO_FRAME = 1;

/**
 * Envelope kind: the presentation time of every frame.
 */
export const KIND_TIMELINE = 3;

/**
 * Envelope kind: one Sample Item of one source.
 */
export const KIND_SAMPLE = 4;

/**
 * Envelope kind: one run of one lane's Frame Map.
 */
export const KIND_FRAME_MAP = 5;

/**
 * Flag bit 0: the frame is a keyframe.
 */
export const FLAG_KEYFRAME = 1;

/**
 * Flag bit 1: the frame is the last of the video.
 */
export const FLAG_LAST_FRAME = 2;

/**
 * Flag bit 2 of a Sample: the head's `timestamp_us` is the sample's time on the video clock.
 */
export const FLAG_SAMPLE_TIME = 4;

/**
 * Video header layout byte: planar Y, U, V with 4:2:0 subsampling.
 */
export const LAYOUT_I420 = 1;

/**
 * Video header layout byte: planar Y, then interleaved U and V.
 */
export const LAYOUT_NV12 = 2;

/**
 * Video header layout byte: one interleaved RGBA plane.
 */
export const LAYOUT_RGBA = 3;

/**
 * Video header layout byte: plane 0 is a JPEG of width x height (full-range BT.601), stride 0.
 */
export const LAYOUT_JPEG = 4;

/**
 * Video header matrix byte: BT.601 YUV to RGB.
 */
export const MATRIX_BT601 = 0;

/**
 * Video header matrix byte: BT.709 YUV to RGB.
 */
export const MATRIX_BT709 = 1;

/**
 * Where the item of a Sample starts: the head plus the sample header, a multiple of 8 so the item's typed views
 * need no copy.
 */
export const SAMPLE_HEADER_BYTES = 40;

/**
 * A Sample's `frame` when it has no frame number in its file, or one that does not fit.
 */
export const SAMPLE_NO_FRAME = 4294967295;

/**
 * Where the sample column of a FrameMap starts: the head plus the map header.
 */
export const FRAME_MAP_HEADER_BYTES = 48;

/**
 * Frames of a map chunk; a run never spans two chunks.
 */
export const FRAME_MAP_RUN_FRAMES = 256;

/**
 * Map cell: nothing belongs to the frame (read as -1 through an `Int32Array`).
 */
export const FRAME_MAP_NONE = 4294967295;

/**
 * Map cell: a dense file covers the frame with nothing (read as -2 through an `Int32Array`).
 */
export const FRAME_MAP_EMPTY = 4294967294;

/**
 * Map kind: the sample is the frame's own; also the kind of a NONE or EMPTY cell.
 */
export const MATCH_EXACT = 0;

/**
 * Map kind: an earlier sample within the tolerance.
 */
export const MATCH_TOLERATED = 1;

/**
 * Map kind: an older sample kept by sticky.
 */
export const MATCH_RETAINED = 2;

/**
 * Upper bound of `data_ahead + data_behind + 1` in a Want.
 */
export const MAX_DATA_WINDOW_FRAMES = 2048;
