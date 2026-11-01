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
//! A frame belongs to the viewer's one video, so the head has no lane. An Overlay (kind 2) is the Drawing
//! Instructions of one lane for one video frame and one view revision; its head carries the frame's `sequence_id`
//! and time, and continues with the overlay header, then sections:
//!
//! ```text
//! 28 u16 lane | 30 u8 match (0 none, 1 exact, 2 tolerated, 3 retained, 4 empty) | 31 pad
//! 32 u32 revision | 36 f32 opacity | 40 u32 target_width | 44 u32 target_height
//! 48 i64 sample_key (frame number or microseconds by the lane's clock; 0 when match is none or empty)
//! 56 u32 section_count | 60 pad
//! 64 sections, each: u16 tag | u16 pad | u32 byte_len | bytes | zero padding to a multiple of 8
//! ```
//!
//! `byte_len` counts the section's bytes only, not the padding: the next section starts at the end of the bytes
//! rounded up to a multiple of 8 (every section, the last included, is padded).
//!
//! Section records are little-endian with 4-byte fields and `f32` coordinates, normalized to the frame; see the
//! `OVERLAY_TAG_*` constants for the tags. Empty sections are omitted and receivers skip tags they do not know:
//!
//! - PAINTS (24 bytes): pen rgb `u8`x3, dashed `u8`, pen_width `f32`, brush rgba `u8`x4, font_family `u32`,
//!   font_size `f32`, radius `f32`
//! - BOXES: x, y, w, h `f32`, paint `u32`; LINES: x1, y1, x2, y2 `f32`, paint; CIRCLES: x, y, r `f32`, paint;
//!   POINTS: x, y `f32`, paint
//! - PATH_POINTS: x, y `f32` pairs; POLYGONS and POLYLINES: first `u32`, count `u32`, paint `u32`
//! - TEXTS: x, y `f32`, text `u32`, anchor `u8`, pad x3, paint `u32`
//! - LABEL_STYLES: size `f32`, family `u32`, background rgba `u8`x4, padding_x, padding_y, radius, gap `f32`
//! - LABELS: x, y `f32`, anchor `u8`, pad x3, style `u32`, first_run `u32`, runs `u32`
//! - RUNS: text `u32`, bar `f32` (NaN for a text run), rgb `u8`x3, weight `u8`
//! - STRINGS: count `u32`, offsets `u32`[count + 1], then the UTF-8 bytes; string ids index it and `u32::MAX` is none

use crate::{ErrorWire, catalogs::CatalogInfoWire};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
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
    PLAYBACK_PROTOCOL: u32 = 3;
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
    /// Envelope kind: the Drawing Instructions of one lane for a frame and a view revision.
    KIND_OVERLAY: u8 = 2;
    /// Envelope kind: the presentation time of every frame.
    KIND_TIMELINE: u8 = 3;
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
    /// Where the sections of an Overlay start: the head plus the overlay header.
    OVERLAY_HEADER_BYTES: u16 = 64;
    /// Overlay match byte: nothing to draw.
    OVERLAY_MATCH_NONE: u8 = 0;
    /// Overlay match byte: the sample is the frame's own.
    OVERLAY_MATCH_EXACT: u8 = 1;
    /// Overlay match byte: an earlier sample within the tolerance.
    OVERLAY_MATCH_TOLERATED: u8 = 2;
    /// Overlay match byte: an older sample kept by sticky.
    OVERLAY_MATCH_RETAINED: u8 = 3;
    /// Overlay match byte: the file covers the frame with nothing.
    OVERLAY_MATCH_EMPTY: u8 = 4;
    /// Overlay section tag: the shared paints.
    OVERLAY_TAG_PAINTS: u16 = 1;
    /// Overlay section tag: boxes.
    OVERLAY_TAG_BOXES: u16 = 2;
    /// Overlay section tag: lines.
    OVERLAY_TAG_LINES: u16 = 3;
    /// Overlay section tag: circles.
    OVERLAY_TAG_CIRCLES: u16 = 4;
    /// Overlay section tag: points.
    OVERLAY_TAG_POINTS: u16 = 5;
    /// Overlay section tag: the points of every polygon and polyline.
    OVERLAY_TAG_PATH_POINTS: u16 = 6;
    /// Overlay section tag: polygons.
    OVERLAY_TAG_POLYGONS: u16 = 7;
    /// Overlay section tag: polylines.
    OVERLAY_TAG_POLYLINES: u16 = 8;
    /// Overlay section tag: texts.
    OVERLAY_TAG_TEXTS: u16 = 9;
    /// Overlay section tag: the shared label styles.
    OVERLAY_TAG_LABEL_STYLES: u16 = 10;
    /// Overlay section tag: labels.
    OVERLAY_TAG_LABELS: u16 = 11;
    /// Overlay section tag: the runs of every label.
    OVERLAY_TAG_RUNS: u16 = 12;
    /// Overlay section tag: the strings every text refers to.
    OVERLAY_TAG_STRINGS: u16 = 13;
    /// Overlay section tags from here up are reserved (64 is the hit targets); receivers skip unknown tags.
    OVERLAY_TAG_RESERVED: u16 = 64;
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
}

/// How one lane shows its overlay.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct LaneViewWire {
    /// The catalog id, such as `built-in:standard`.
    pub catalog: String,
    /// The class filter toggles by option id; options not listed keep their default.
    #[ts(type = "Record<string, boolean>")]
    pub filter: BTreeMap<String, bool>,
    /// How far back a time-keyed sample may be, in milliseconds; `null` is exact.
    pub tolerance_ms: Option<u32>,
    /// Whether old samples stay on screen.
    pub sticky: StickyWire,
    /// The lane's displayed image size.
    pub target: TargetWire,
    /// Whether the lane is shown.
    pub visible: bool,
}

/// Keeping the last sample on screen while no sample matches.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct StickyWire {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be, in milliseconds, at most 100000; `null` is without limit.
    pub timeout_ms: Option<u32>,
    /// How opaque a kept sample is drawn, 0 to 1.
    pub opacity: f32,
}

/// A displayed image size in CSS pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct TargetWire {
    /// Width in CSS pixels.
    pub width: u32,
    /// Height in CSS pixels.
    pub height: u32,
}

/// One lane of the opened entry.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct LaneInfo {
    /// The lane's name.
    pub label: String,
    /// Whether the lane has an overlay file; the lane of an entry without overlays is plain.
    pub has_overlay: bool,
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
    },
    /// Replaces the viewer's window.
    Want(Want),
    /// Replaces how one lane shows its overlay; a higher revision resends the lane's overlays.
    LaneView {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// Counts the lane's changes; overlays carry the revision they were made for.
        revision: u32,
        /// The new view.
        view: LaneViewWire,
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
        /// The view every lane starts with.
        lane_defaults: LaneViewWire,
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
    /// A lane's catalog could not be compiled or failed on a frame; the lane keeps its last catalog. Only for an
    /// audience with host details.
    LaneProblem {
        /// The lane's position in `Opened.lanes`.
        lane: u16,
        /// What went wrong, in words for the user.
        reason: String,
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
    /// The session cannot go on and ends.
    Error(ErrorWire),
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn messages_use_the_names_the_frontend_parses() {
        let open = ClientMessage::Open {
            ticket: "t1".into(),
            start_frame: None,
        };
        assert_eq!(
            serde_json::to_value(&open).unwrap(),
            json!({"type": "Open", "ticket": "t1"})
        );
        let want = json!({
            "type": "Want", "playhead": 7, "ahead": 20, "behind": 5, "direction": "backward"
        });
        assert!(matches!(
            serde_json::from_value(want).unwrap(),
            ClientMessage::Want(Want {
                playhead: 7,
                direction: Direction::Backward,
                ..
            })
        ));
        let lane_defaults = LaneViewWire {
            catalog: "built-in:standard".into(),
            filter: BTreeMap::new(),
            tolerance_ms: Some(50),
            sticky: StickyWire {
                enabled: false,
                timeout_ms: Some(2050),
                opacity: 1.0,
            },
            target: TargetWire {
                width: 1280,
                height: 720,
            },
            visible: true,
        };
        let opened = ServerMessage::Opened {
            protocol: PLAYBACK_PROTOCOL,
            frame_count: 90,
            duration_us: 3_000_000,
            frame_bytes: 4608,
            width: 64,
            height: 48,
            warnings: Vec::new(),
            lanes: vec![LaneInfo {
                label: "gate.mp4".into(),
                has_overlay: false,
            }],
            lane_defaults,
            catalogs: vec![CatalogInfoWire {
                id: "built-in:standard".into(),
                name: "Standard".into(),
                description: String::new(),
            }],
        };
        let json = serde_json::to_value(&opened).unwrap();
        assert_eq!(json["protocol"], 3);
        assert_eq!(json["catalogs"][0]["id"], "built-in:standard");
        assert_eq!(json["type"], "Opened");
        assert_eq!(
            json["lanes"],
            json!([{"label": "gate.mp4", "has_overlay": false}])
        );
        assert_eq!(json["lane_defaults"]["sticky"]["timeout_ms"], 2050);
        let status = ServerMessage::LaneStatus {
            lane: 1,
            status: LaneStatusWire::Failed(ErrorWire {
                code: foundation::ErrorCode::OverlayInvalid,
                params: crate::ErrorParams::default(),
                reason: None,
                location: None,
            }),
        };
        assert_eq!(
            serde_json::to_value(&status).unwrap(),
            json!({"type": "LaneStatus", "lane": 1, "status": {"status": "Failed", "code": "OverlayInvalid", "params": {}}})
        );
    }
}
