//! Wire types and envelope constants for the playback WebSocket.
//!
//! Text frames carry the JSON messages below; binary frames carry envelopes. Every envelope starts with a fixed
//! 32-byte head, little-endian:
//!
//! ```text
//! 0 u32 magic | 4 u8 version | 5 u8 kind | 6 u16 header_len (payload start offset) | 8 u32 lane
//! 12 u64 sequence_id | 20 i64 timestamp_us | 28 u32 flags
//! ```
//!
//! A VideoFrame (kind 1) continues the head with the video header, and its planes start at `header_len`
//! (at least [`VIDEO_FRAME_HEADER_BYTES`]):
//!
//! ```text
//! 32 u32 width | 36 u32 height | 40 u8 layout | 41 u8 matrix | 42 u8 range (0 limited) | 43 pad
//! 44 u32 stride[3] | 56 u32 plane_len[3] | planes, one after the other
//! ```
//!
//! A Timeline (kind 3) has its `f64` times in microseconds, by sequence id, from `header_len` (at least
//! [`ENVELOPE_HEAD_BYTES`]) to the end. Kind 2 is reserved for overlays.

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
    PLAYBACK_PROTOCOL: u32 = 1;
    /// "AXDV" read as a little-endian u32.
    ENVELOPE_MAGIC: u32 = 0x5644_5841;
    /// The envelope format version, byte 4.
    ENVELOPE_VERSION: u8 = 1;
    /// Bytes of the fixed envelope head.
    ENVELOPE_HEAD_BYTES: u16 = 32;
    /// Where the planes of a VideoFrame start, at least; the head plus the video header.
    VIDEO_FRAME_HEADER_BYTES: u16 = 68;
    /// Envelope kind: one decoded video frame.
    KIND_VIDEO_FRAME: u8 = 1;
    /// Envelope kind: overlay data for a frame; reserved for M2, receivers ignore it.
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

/// What the frontend sends.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum ClientMessage {
    /// Opens a Content; the first message of a session.
    Open {
        /// The Content to open, by the id the workspace gave it.
        content_id: String,
        /// The frame to show, paused; the first frame when absent.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(type = "number", optional)]
        start_frame: Option<u64>,
    },
    /// Replaces the viewer's window.
    Want(Want),
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
    },
    /// One frame could not be decoded; the others are fine. The viewer names the frame itself, so `reason` is only
    /// the cause.
    FrameError {
        /// The frame that failed.
        #[ts(type = "number")]
        sequence_id: u64,
        /// What went wrong, in words for the user.
        reason: String,
    },
    /// The session cannot go on and ends.
    Error {
        /// What went wrong, in words for the user.
        reason: String,
        /// What it is about, such as the file path.
        location: String,
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn messages_use_the_names_the_frontend_parses() {
        let open = ClientMessage::Open {
            content_id: "c1".into(),
            start_frame: None,
        };
        assert_eq!(
            serde_json::to_value(&open).unwrap(),
            json!({"type": "Open", "content_id": "c1"})
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
        let opened = ServerMessage::Opened {
            protocol: PLAYBACK_PROTOCOL,
            frame_count: 90,
            duration_us: 3_000_000,
            frame_bytes: 4608,
        };
        assert_eq!(
            serde_json::to_value(&opened).unwrap(),
            json!({
                "type": "Opened", "protocol": 1, "frame_count": 90, "duration_us": 3_000_000, "frame_bytes": 4608
            })
        );
    }
}
