//! The constants of the playback socket's messages and envelopes, defined once for Rust and for the TypeScript export.

use overlay_sync::MatchKind;

/// Defines constants once for Rust and for `export-types`, which writes them to `api.ts`.
macro_rules! constants {
    ($($(#[doc = $doc:literal])+ $name:ident: $type:ty = $value:expr;)+) => {
        $($(#[doc = $doc])+ pub const $name: $type = $value;)+
        /// Every constant above with its doc, in declaration order, for the TypeScript export.
        pub const CONSTANTS: &[(&str, u64, &str)] = &[$((stringify!($name), $name as u64, concat!($($doc, "\n"),+))),+];
    };
}
pub(crate) use constants;

constants! {
    /// Raised when the messages or the envelope change in a way an older frontend cannot read, or a message is added
    /// that an older backend would reject.
    PLAYBACK_PROTOCOL: u32 = 11;
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
    /// Flag bit 2 of a Sample: the head's `timestamp_us` is the sample's time on the video clock.
    FLAG_SAMPLE_TIME: u32 = 4;
    /// Video header layout byte: planar Y, U, V with 4:2:0 subsampling.
    LAYOUT_I420: u8 = 1;
    /// Video header layout byte: planar Y, then interleaved U and V.
    LAYOUT_NV12: u8 = 2;
    /// Video header layout byte: one interleaved RGBA plane.
    LAYOUT_RGBA: u8 = 3;
    /// Video header layout byte: plane 0 is a JPEG of width x height (full-range BT.601), stride 0.
    LAYOUT_JPEG: u8 = 4;
    /// Video header matrix byte: BT.601 YUV to RGB.
    MATRIX_BT601: u8 = 0;
    /// Video header matrix byte: BT.709 YUV to RGB.
    MATRIX_BT709: u8 = 1;
    /// Where the item of a Sample starts: the head plus the sample header, a multiple of 8 so the item's typed views
    /// need no copy.
    SAMPLE_HEADER_BYTES: u16 = 40;
    /// A Sample's `frame` when it has no frame number in its file, or one that does not fit.
    SAMPLE_NO_FRAME: u32 = u32::MAX;
    /// Where the sample column of a FrameMap starts: the head plus the map header.
    FRAME_MAP_HEADER_BYTES: u16 = 48;
    /// Frames of a map chunk; a run never spans two chunks.
    FRAME_MAP_RUN_FRAMES: u16 = overlays::RUN_FRAMES;
    /// Map cell: nothing belongs to the frame (read as -1 through an `Int32Array`).
    FRAME_MAP_NONE: u32 = overlays::NONE_CELL;
    /// Map cell: a dense file covers the frame with nothing (read as -2 through an `Int32Array`).
    FRAME_MAP_EMPTY: u32 = overlays::EMPTY_CELL;
    /// Map kind: the sample is the frame's own; also the kind of a NONE or EMPTY cell.
    MATCH_EXACT: u8 = MatchKind::Exact as u8;
    /// Map kind: an earlier sample within the tolerance.
    MATCH_TOLERATED: u8 = MatchKind::Tolerated as u8;
    /// Map kind: an older sample kept by sticky.
    MATCH_RETAINED: u8 = MatchKind::Retained as u8;
    /// Upper bound of `data_ahead + data_behind + 1` in a Want.
    MAX_DATA_WINDOW_FRAMES: u32 = 2048;
}
