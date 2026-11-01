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
//! sample index and `timestamp_us` is reserved (0) for the capture time of live samples:
//!
//! ```text
//! 28 u16 source (the viewer's source id, `LaneInfo::source`) | 30 u16 reserved
//! 32 u32 data_revision (the source revision the item belongs to) | 36 u32 reserved
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

use crate::{ErrorWire, catalogs::CatalogInfoWire, inspect::InspectionWire};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Defines the envelope constants once for Rust and for `export-types`, which writes them to `api.ts`.
macro_rules! constants {
    ($($(#[doc = $doc:literal])+ $name:ident: $type:ty = $value:expr;)+) => {
        $($(#[doc = $doc])+ pub const $name: $type = $value;)+
        /// Every constant above with its doc, in declaration order, for the TypeScript export.
        pub const CONSTANTS: &[(&str, u64, &str)] = &[$((stringify!($name), $name as u64, concat!($($doc, "\n"),+))),+];
    };
}

constants! {
    /// Raised when the messages or the envelope change in a way an older frontend cannot read.
    PLAYBACK_PROTOCOL: u32 = 5;
    /// "AXDV" read as a little-endian u32.
    ENVELOPE_MAGIC: u32 = 0x5644_5841;
    /// The envelope format version, byte 4.
    ENVELOPE_VERSION: u8 = 1;
    /// Bytes of the fixed envelope head.
    ENVELOPE_HEAD_BYTES: u16 = 28;
    /// Where the times of a Timeline start: the head padded to a multiple of 8 for the `f64` values.
    TIMELINE_HEADER_BYTES: u16 = 32;
    /// Where the planes of a VideoFrame start, at least; the head plus the video header.
    VIDEO_FRAME_HEADER_BYTES: u16 = 64;
    /// Envelope kind: one decoded video frame.
    KIND_VIDEO_FRAME: u8 = 1;
    /// Envelope kind: the presentation time of every frame.
    KIND_TIMELINE: u8 = 3;
    /// Envelope kind: one Sample Item of one source.
    KIND_SAMPLE: u8 = 4;
    /// Envelope kind: one run of one lane's Frame Map.
    KIND_FRAME_MAP: u8 = 5;
    /// Flag bit 0: the frame is a keyframe.
    FLAG_KEYFRAME: u32 = 1;
    /// Flag bit 1: the frame is the last of the video.
    FLAG_LAST_FRAME: u32 = 2;
    /// Video header layout byte: planar Y, U, V with 4:2:0 subsampling.
    LAYOUT_I420: u8 = 1;
    /// Video header layout byte: planar Y, then interleaved U and V.
    LAYOUT_NV12: u8 = 2;
    /// Video header layout byte: one interleaved RGBA plane.
    LAYOUT_RGBA: u8 = 3;
    /// Video header matrix byte: BT.601 YUV to RGB.
    MATRIX_BT601: u8 = 0;
    /// Video header matrix byte: BT.709 YUV to RGB.
    MATRIX_BT709: u8 = 1;
    /// Where the item of a Sample starts: the head plus the sample header, a multiple of 8 so the item's typed views
    /// need no copy.
    SAMPLE_HEADER_BYTES: u16 = 40;
    /// Where the sample column of a FrameMap starts: the head plus the map header.
    FRAME_MAP_HEADER_BYTES: u16 = 48;
    /// Frames of a map chunk; a run never spans two chunks.
    FRAME_MAP_RUN_FRAMES: u16 = 256;
    /// Map cell: nothing belongs to the frame (read as -1 through an `Int32Array`).
    FRAME_MAP_NONE: u32 = 0xFFFF_FFFF;
    /// Map cell: a dense file covers the frame with nothing (read as -2 through an `Int32Array`).
    FRAME_MAP_EMPTY: u32 = 0xFFFF_FFFE;
    /// Map kind: the sample is the frame's own; also the kind of a NONE or EMPTY cell.
    MATCH_EXACT: u8 = 0;
    /// Map kind: an earlier sample within the tolerance.
    MATCH_TOLERATED: u8 = 1;
    /// Map kind: an older sample kept by sticky.
    MATCH_RETAINED: u8 = 2;
    /// Upper bound of `data_ahead + data_behind + 1` in a Want.
    MAX_DATA_WINDOW_FRAMES: u32 = 2048;
}

/// How a viewer steps through the video.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    /// Playing or stepping forward.
    Forward,
    /// Stepping or scrubbing backward.
    Backward,
}

/// The frames a viewer holds or wants: its window around the playhead. Each want replaces the viewer's previous one;
/// the backend works on what is nearest the newest playhead first.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct Want {
    /// The sequence id the viewer shows or is about to.
    #[ts(type = "number")]
    pub playhead: u64,
    /// How many frames after the playhead the viewer keeps.
    pub ahead: u32,
    /// How many frames before the playhead the viewer keeps.
    pub behind: u32,
    /// Which way the viewer is travelling, which decides what to prefetch first.
    pub direction: Direction,
    /// How many frames after the playhead the viewer keeps Frame Maps and Sample Items for.
    pub data_ahead: u32,
    /// How many frames before the playhead the viewer keeps Frame Maps and Sample Items for.
    pub data_behind: u32,
}

/// How a lane selects samples for frames: the part of a lane's settings the backend needs.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LanePolicyWire {
    /// How far back a time-keyed sample may be, in milliseconds; `null` is exact.
    pub tolerance_ms: Option<u32>,
    /// Whether old samples stay on screen.
    pub sticky: StickyWire,
}

/// Keeping the last sample on screen while no sample matches.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct StickyWire {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be, in milliseconds, at most 100000; `null` is without limit.
    pub timeout_ms: Option<u32>,
}

/// What every lane starts with.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LaneDefaultsWire {
    /// The catalog id, such as `built-in:standard`.
    pub catalog: String,
    /// The sample selection every lane starts with.
    pub policy: LanePolicyWire,
}

/// One lane of the opened entry.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LaneInfo {
    /// The lane's name.
    pub label: String,
    /// The viewer's id of the source the lane draws; `null` for a plain lane. Lanes on the same file share one.
    pub source: Option<u16>,
}

/// The clock a lane's samples are on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum ClockWire {
    /// Frame numbers.
    Frame,
    /// Timestamps.
    Timestamp,
}

/// One class filter a lane offers.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct FilterOptionInfo {
    /// Stable id, such as `show_car`.
    pub id: String,
    /// What the user reads.
    pub label: String,
    /// Whether the filter starts switched on.
    pub default_enabled: bool,
}

/// A problem that did not stop an overlay from loading.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct WarningInfo {
    /// What happened.
    pub problem: String,
    /// The first reasons.
    pub reasons: Vec<String>,
    /// What the user can expect as a result.
    pub consequence: String,
    /// Whether more reasons exist than are listed.
    pub more_reasons: bool,
}

/// Where a lane's load stands.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum LaneStatusWire {
    /// The overlay file is being decoded.
    Loading,
    /// The overlay is ready to draw.
    Ready {
        /// The clock the file's samples are on.
        clock: ClockWire,
        /// The class filters the file's content calls for.
        filter: Vec<FilterOptionInfo>,
        /// Problems the decoder reported.
        warnings: Vec<WarningInfo>,
        /// How many samples the file has.
        #[ts(type = "number")]
        samples: u64,
        /// The source's revision; its Sample Items and the lane's Frame Maps carry it.
        data_revision: u32,
        /// The mean size of a Sample Item in bytes, 0 for an empty file; sizes the viewer's data window.
        mean_sample_bytes: u32,
    },
    /// The overlay could not be loaded, and why.
    Failed(ErrorWire),
}

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
        /// Total payload bytes of one frame: the sum of its plane lengths (planes are tightly packed).
        #[ts(type = "number")]
        frame_bytes: u64,
        /// Width of the decoded frames in pixels.
        width: u32,
        /// Height of the decoded frames in pixels.
        height: u32,
        /// Problems with the video that did not stop it from opening. Only for an audience with host details.
        warnings: Vec<String>,
        /// The lanes of the entry, in order.
        lanes: Vec<LaneInfo>,
        /// What every lane starts with.
        lane_defaults: LaneDefaultsWire,
        /// The catalogs a lane may draw with, built-ins first and Standard first among them.
        catalogs: Vec<CatalogInfoWire>,
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
    /// The session cannot go on and ends.
    Error(ErrorWire),
}

#[cfg(test)]
mod tests;
