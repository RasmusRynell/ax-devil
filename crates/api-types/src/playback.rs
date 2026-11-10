//! Wire types and envelope constants for the playback WebSocket.
//!
//! Text frames carry the JSON messages below; binary frames carry envelopes. Every envelope starts with a fixed
//! 28-byte head, little-endian:
//!
//! ```text
//! 0 u32 magic | 4 u8 version | 5 u8 kind | 6 u16 header_len (payload start offset)
//! 8 u64 sequence_id | 16 i64 timestamp_us | 24 u32 flags
//! ```
//!
//! A VideoFrame (kind 1) continues the head with the video header, and its planes start at `header_len`
//! (at least [`VIDEO_FRAME_HEADER_BYTES`]):
//!
//! ```text
//! 28 u32 width | 32 u32 height | 36 u8 layout | 37 u8 matrix | 38 u8 range (0 limited) | 39 pad
//! 40 u32 stride[3] | 52 u32 plane_len[3] | planes, one after the other
//! ```
//!
//! A Timeline (kind 3) has its `f64` times in microseconds, by sequence id, from `header_len` (at least
//! [`ENVELOPE_HEAD_BYTES`]; the backend sends [`TIMELINE_HEADER_BYTES`] so the times are 8-byte aligned) to the end.
//!
//! A frame belongs to the viewer's one video, so the head has no lane. A Sample (kind 4) is one Sample Item of one
//! source; the item is the self-describing column format of `scene::serial::columns`. Its head's `sequence_id` is the
//! sample index, and `timestamp_us` is the sample's time on the video clock when [`FLAG_SAMPLE_TIME`] is set (0
//! otherwise):
//!
//! ```text
//! 28 u16 source (the viewer's source id, `LaneInfo::source`) | 30 u16 reserved
//! 32 u32 data_revision (the source revision the item belongs to)
//! 36 u32 frame (the sample's frame number in its file, or SAMPLE_NO_FRAME)
//! 40 the item, to the end of the message
//! ```
//!
//! A FrameMap (kind 5) is one run of one lane's map from frame to sample; its head's `sequence_id` is the first frame.
//! A run never spans two [`FRAME_MAP_RUN_FRAMES`] chunks:
//!
//! ```text
//! 28 u16 lane | 30 u16 reserved | 32 u32 policy_revision | 36 u32 data_revision
//! 40 u32 count (1 to 256) | 44 u32 reserved
//! 48 u32 sample[count] (a sample index, FRAME_MAP_NONE or FRAME_MAP_EMPTY) | u8 kind[count] (MATCH_*)
//! ```
//!
//! The backend alone decides what the viewer holds: it drops a Sample Item only on `Evict`, a new data revision or a
//! failed source, and a Frame Map cell only outside the last `DataWindow` or on a revision change. `Open.data_bytes`
//! bounds the Sample Items the backend keeps sent.

use crate::{
    ErrorWire,
    catalogs::CatalogInfoWire,
    inspect::{InspectionWire, ObjectItemsWire},
    live::{LiveAction, LiveEventsWire, LiveGapWire, LiveOpenedWire, LiveStatusWire},
};
use scene_store::{Search, Sort};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

pub mod envelope;
mod events;
mod export;
mod file_scope;
mod lane;
mod want;
mod wire_constants;
pub use events::*;
pub use export::*;
pub use file_scope::*;
pub use lane::*;
pub use want::*;
pub use wire_constants::*;

/// What the frontend sends.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum ClientMessage {
    /// Opens the stream a ticket names; the first message of a session, within 5 seconds of connecting. The ticket
    /// comes from the Session socket's `RequestTicket`, works once and is never put in a URL.
    Open {
        /// The ticket.
        ticket: String,
        /// The frame to show, paused; the first frame when absent.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(type = "number", optional)]
        start_frame: Option<u64>,
        /// The most bytes of Sample Items the viewer holds at once, fixed for the stream. The backend never has more
        /// than this sent, except one item larger than it alone.
        #[ts(type = "number")]
        data_bytes: u64,
    },
    /// Replaces the viewer's window.
    Want(Want),
    /// Replaces how one lane selects samples and whether it wants any; a higher revision resends the lane's Frame Maps.
    LanePolicy {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// Counts the lane's changes; Frame Maps carry the revision they were selected under.
        revision: u32,
        /// How samples are selected.
        policy: LanePolicyWire,
        /// Whether the lane is shown; an unsubscribed lane gets no Frame Maps and its samples are not sent for it.
        subscribed: bool,
    },
    /// Names the entry of the Content that the viewer is likely to open next, and the labels of the lanes it would
    /// show, so the backend can ready the entry's caches in the background while this one plays. A newer message
    /// replaces the older; `entry` of `None`, an entry the Content does not have or the setting being off prepares
    /// nothing. Nothing is sent back.
    Prepare {
        /// The entry's id, `EntryInfo::id`.
        entry: Option<String>,
        /// The labels of the lanes to prepare, `EntryInfo::lanes`.
        lanes: Vec<String>,
    },
    /// Asks for a catalog document by id; the answer is a `Catalog` message.
    Catalog {
        /// The catalog id, such as `built-in:standard`.
        id: String,
    },
    /// Asks for every field of one object of a Sample Item the viewer holds; the answer is `Inspected`. A newer
    /// request replaces one not started yet.
    Inspect {
        /// The viewer's source id, `LaneInfo::source`.
        source: u16,
        /// The source revision the sample belongs to.
        data_revision: u32,
        /// The sample's index in its source.
        sample: u32,
        /// The object's id; at most 1024 bytes.
        entity: String,
    },
    /// Asks for the leaves every object of a Sample Item the viewer holds has at the query paths named, for a search
    /// of the objects' fields; the answer is `InspectedFields`. A newer request replaces one not started yet.
    InspectFields {
        /// The viewer's source id, `LaneInfo::source`.
        source: u16,
        /// The source revision the sample belongs to.
        data_revision: u32,
        /// The sample's index in its source.
        sample: u32,
        /// The paths a search names, in its spelling; at most 64, each at most 1024 bytes.
        paths: Vec<String>,
    },
    /// Starts an export of the viewer's video with the lanes named, frozen as the viewer holds them; progress and the
    /// result arrive as `Export` messages. Owners only; one export runs per backend.
    Export {
        /// Names the job in its `Export` messages.
        job: u32,
        /// The lanes to draw, in the order they tile.
        lanes: Vec<ExportLaneWire>,
        /// The first frame, a sequence id.
        #[ts(type = "number")]
        first: u64,
        /// The last frame, inclusive; equal to `first` for a PNG.
        #[ts(type = "number")]
        last: u64,
        /// What to write.
        format: ExportFormatWire,
        /// Where to write it on the backend's machine: an absolute path.
        path: String,
    },
    /// Cancels a running export; ignored when the job is not running.
    CancelExport {
        /// The job to cancel.
        job: u32,
    },
    /// Asks for rows of a lane's File scope list: the objects its whole file holds that the video shows under the
    /// lane's matching, kept by the class toggles and the search, sorted and paged. The answer is `File`. One request
    /// runs per lane; a newer `request` replaces the older, which is never answered. Owners only. A `limit` outside 1 to 200 or `bins` above 256 is a `LaneProblem`.
    FileQuery {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// Names the request in its answer; the client drops answers of older requests.
        request: u32,
        /// The search, parsed by the client.
        query: Search,
        /// The lane's class toggles: bit `i % 32` of word `i / 32` is set for each enabled filter option, as in the
        /// Sample Item's class mask.
        classes: Vec<u32>,
        /// The order of the rows.
        sort: Sort,
        /// The first row to send.
        offset: u32,
        /// How many rows to send at most, from 1 to 200.
        limit: u32,
        /// The most spans of presence per row, from 0 to 256; 0 sends none.
        bins: u16,
        /// An object whose row in the list is wanted whatever the page: the answer's `located`.
        locate: Option<String>,
    },
    /// Stops wanting a lane's File scope list: the lane's running or waiting query is cancelled and never answered, and
    /// what the backend kept for the lane's paging is dropped. Nothing is sent back. A lane the video
    /// does not have ends the session, as for `FileQuery`.
    FileRelease {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
    },
    /// Pauses, resumes or retries a live feed. Ignored unless the holder's authority is its own.
    Live {
        /// What to do.
        action: LiveAction,
    },
    /// Asks where one object of a lane's file is on the video: the answer is `File`. Independent of the lane's
    /// File scope list, which it does not cancel; a newer `request` for the lane stops an older one still running,
    /// which is then not answered. The lane's class toggles do not matter. Owners only.
    Appearances {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// Names the request in its answer; the client drops answers of older requests.
        request: u32,
        /// The object's id.
        entity: String,
    },
    /// Asks for rows of a lane's event list; the answer is `File`.
    EventQuery(EventQueryWire),
    /// Asks for one event of a lane's file; the answer is `File`.
    Event(EventAskWire),
    /// Ends the session and releases the source.
    Close,
}

/// What the backend sends as text. `Opened` is followed by a Timeline envelope.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum ServerMessage {
    /// The frame index is being built.
    Indexing {
        /// How far along it is, from 0 to 1.
        progress: f32,
    },
    /// The Content is open.
    Opened {
        /// The protocol version, to compare with `PLAYBACK_PROTOCOL`.
        protocol: u32,
        /// How many frames the video has.
        #[ts(type = "number")]
        frame_count: u64,
        /// Video length in microseconds.
        #[ts(type = "number")]
        duration_us: i64,
        /// Bytes one frame takes once the viewer holds it: the sum of its plane lengths (planes are tightly packed),
        /// or width x height x 4 of the delivered size when frames arrive as JPEG and are decoded to RGBA.
        #[ts(type = "number")]
        frame_bytes: u64,
        /// Width of the decoded frames in pixels.
        width: u32,
        /// Height of the decoded frames in pixels.
        height: u32,
        /// Frames per second the video's stream declares; `null` when it declares none.
        fps: Option<f64>,
        /// Problems with the video that did not stop it from opening. Only for an audience with host details.
        warnings: Vec<String>,
        /// The lanes of the entry, in order.
        lanes: Vec<LaneInfo>,
        /// What every lane starts with.
        lane_defaults: LaneDefaultsWire,
        /// The catalogs a lane may draw with, built-ins first and Standard first among them.
        catalogs: Vec<CatalogInfoWire>,
        /// The video's PTS time base and first PTS; `null` for an image sequence.
        video_pts: Option<VideoPtsWire>,
        /// How evenly the video's frames are spaced; `null` for a one-frame video.
        cadence: Option<CadenceWire>,
    },
    /// A lane's overlay load changed.
    LaneStatus {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// Where the load stands.
        status: LaneStatusWire,
    },
    /// A lane's policy was refused or one of its samples could not be read; the lane keeps what it had. Only for an
    /// audience with host details.
    LaneProblem {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// What went wrong, in words for the user.
        reason: String,
    },
    /// The answer to a `Catalog` request.
    Catalog {
        /// The requested id.
        id: String,
        /// Counts the document's changes; 0 when there is no such catalog.
        revision: u32,
        /// The catalog document, or `null` when no such catalog exists or it cannot be served.
        document: Option<String>,
        /// Why an existing catalog cannot be served (too large, unreadable, not text); `null` otherwise.
        problem: Option<String>,
    },
    /// The catalogs a lane may draw with changed: the whole list, as in `Opened.catalogs`.
    Catalogs {
        /// Built-ins first and Standard first among them, then the user's.
        catalogs: Vec<CatalogInfoWire>,
    },
    /// The answer to an `Inspect` request.
    Inspected {
        /// The requested source.
        source: u16,
        /// The requested revision.
        data_revision: u32,
        /// The requested sample.
        sample: u32,
        /// The requested object.
        entity: String,
        /// What the backend knows of it.
        inspection: InspectionWire,
    },
    /// The answer to an `InspectFields` request.
    InspectedFields {
        /// The requested source.
        source: u16,
        /// The requested revision.
        data_revision: u32,
        /// The requested sample.
        sample: u32,
        /// The requested paths.
        paths: Vec<String>,
        /// The objects that have a leaf at the paths; `null` when the viewer does not hold this sample under this
        /// revision, its authority no longer reaches it, or the search is more than the backend will do over it.
        objects: Option<Vec<ObjectItemsWire>>,
    },
    /// One frame could not be decoded; the others are fine. The viewer names the frame itself, so the error is only
    /// the cause.
    FrameError {
        /// The frame that failed.
        #[ts(type = "number")]
        sequence_id: u64,
        /// Why it failed.
        #[serde(flatten)]
        #[ts(flatten)]
        error: ErrorWire,
    },
    /// The frames the viewer keeps Frame Map cells for from now on, inclusive: the data window the backend applied.
    /// Precedes every Frame Map run selected for it.
    DataWindow {
        /// The first frame.
        #[ts(type = "number")]
        first: u64,
        /// The last frame.
        #[ts(type = "number")]
        last: u64,
    },
    /// The frames the backend holds decoded for the video, which the timeline shades. Sent when they change, at most
    /// every 500 ms.
    Cached {
        /// Sorted, disjoint, non-adjacent inclusive runs `[first, last]` of sequence ids.
        #[ts(type = "Array<[number, number]>")]
        ranges: Vec<[u64; 2]>,
    },
    /// The viewer no longer holds these samples of the source. Ignored for another data revision; a sample not held
    /// is ignored.
    Evict {
        /// The viewer's source id, `LaneInfo::source`.
        source: u16,
        /// The source revision the samples belong to.
        data_revision: u32,
        /// The sample indices, ascending and distinct.
        samples: Vec<u32>,
    },
    /// Where an export stands. Sent at most ten times a second while running, then once with the outcome.
    Export {
        /// The job from the request.
        job: u32,
        /// Where it stands.
        state: ExportStateWire,
    },
    /// The answer to a File scope request that was not replaced; not held back for a frame.
    File {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// The request answered.
        request: u32,
        /// What the file holds, or why it cannot be asked.
        answer: FileAnswer,
    },
    /// A live stream is open: the first message of its socket, instead of `Opened`. See [`crate::live`].
    LiveOpened(LiveOpenedWire),
    /// Where a live stream's feeds stand.
    LiveStatus(LiveStatusWire),
    /// Events a live frame made visible, for the Events list.
    LiveEvents(LiveEventsWire),
    /// Samples a live lane lost, for the Events list.
    LiveGap(LiveGapWire),
    /// The session cannot go on and ends.
    Error(ErrorWire),
}

/// Visits the types this module sends to the frontend, and its modules' types.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<ClientMessage>();
    types.visit::<ServerMessage>();
    events::visit_api_types(types);
    export::visit_api_types(types);
    file_scope::visit_api_types(types);
    lane::visit_api_types(types);
    want::visit_api_types(types);
}

#[cfg(test)]
mod tests;
