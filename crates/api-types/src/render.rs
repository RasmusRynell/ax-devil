//! The private protocol between the backend and the hidden render page that draws an export's overlays.
//!
//! The page connects to a loopback WebSocket and sends `Hello` as text. The backend answers with `Setup`; the page
//! replies `Ready` with the output size or `Failed`. Then, for each frame, the backend sends the Sample envelopes
//! (kind 4) the page does not hold yet and the frame's picture (kind 1) as binary messages, in the playback format,
//! followed by a `Render` text message. The page holds the items named by the previous `Render`, so the backend sends
//! only new ones. The page answers each `Render` with one binary Pixels message, little-endian:
//!
//! ```text
//! 0 u32 magic | 4 u32 format (PIXELS_*) | 8 u64 seq (the Render's) | 16 u32 width | 20 u32 height
//! 24 u32 stride0 | 28 u32 stride1 | 32 the planes, rows top-down
//! ```
//!
//! I420 has Y of `stride0` bytes a row, then U, then V of `stride1` bytes a row each (width and height even, chroma
//! half of both). RGBA has one plane of `stride0` bytes a row and `stride1` is 0.

use crate::playback::constants;
use bytes::Bytes;
use export::{Output, Pixels, RenderSetup};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

constants! {
    /// Raised when the messages or the Pixels layout change in a way an older render page cannot use.
    RENDER_PROTOCOL: u32 = 1;
    /// "AXPX" read as a little-endian u32.
    PIXELS_MAGIC: u32 = 0x5850_5841;
    /// Bytes before the planes of a Pixels message.
    PIXELS_HEADER_BYTES: u32 = 32;
    /// Pixels format: planar Y, U, V with 4:2:0 subsampling, BT.709 limited range.
    PIXELS_I420: u32 = 1;
    /// Pixels format: one interleaved RGBA plane.
    PIXELS_RGBA: u32 = 2;
}

/// What the page sends as text.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum PageMessage {
    /// The first message: the page proves it was started by the backend.
    Hello {
        /// The protocol version, to compare with `RENDER_PROTOCOL`.
        protocol: u32,
        /// The secret the backend gave the page process.
        secret: String,
    },
    /// The page is set up for the last `Setup`.
    Ready {
        /// Width of the output; even for I420.
        width: u32,
        /// Height of the output; even for I420.
        height: u32,
        /// The GPU or software renderer in use, for the log.
        renderer: String,
    },
    /// The page cannot render, and why; the job fails.
    Failed {
        /// What went wrong, in words for the user.
        reason: String,
    },
}

/// What the backend sends as text, besides the binary envelopes.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum HostMessage {
    /// Describes the job; answered by `Ready`.
    Setup(RenderSetup),
    /// Draws one frame from the picture and Sample envelopes held; answered by a Pixels message.
    Render {
        /// Echoed in the Pixels message.
        #[ts(type = "number")]
        seq: u64,
        /// The sample index each lane draws, -1 when nothing belongs to the frame or -2 when a dense file covers it with
        /// nothing (a Frame Map cell read through an `Int32Array`).
        cells: Vec<i32>,
        /// The `MATCH_*` kind of each lane's cell.
        kinds: Vec<u8>,
    },
}

/// The Pixels message that carries `pixels`: the header, then the planes.
pub fn pixels_message(pixels: &Pixels) -> Bytes {
    let format = match pixels.output {
        Output::I420 => PIXELS_I420,
        Output::Rgba => PIXELS_RGBA,
    };
    let mut bytes = Vec::with_capacity(PIXELS_HEADER_BYTES as usize + pixels.data.len());
    bytes.extend_from_slice(&PIXELS_MAGIC.to_le_bytes());
    bytes.extend_from_slice(&format.to_le_bytes());
    bytes.extend_from_slice(&pixels.seq.to_le_bytes());
    for field in [pixels.width, pixels.height, pixels.stride0, pixels.stride1] {
        bytes.extend_from_slice(&field.to_le_bytes());
    }
    bytes.extend_from_slice(&pixels.data);
    bytes.into()
}

/// Reads a Pixels message. The planes are the message's own bytes, not a copy. `None` when it is shorter than its
/// header, has the wrong magic or an unknown format; whether the planes fit the layout is for the caller to check.
pub fn parse_pixels(message: &Bytes) -> Option<Pixels> {
    let u32_at = |at: usize| {
        Some(u32::from_le_bytes(
            message.get(at..at + 4)?.try_into().ok()?,
        ))
    };
    if message.len() < PIXELS_HEADER_BYTES as usize || u32_at(0)? != PIXELS_MAGIC {
        return None;
    }
    let output = match u32_at(4)? {
        PIXELS_I420 => Output::I420,
        PIXELS_RGBA => Output::Rgba,
        _ => return None,
    };
    Some(Pixels {
        seq: u64::from_le_bytes(message.get(8..16)?.try_into().ok()?),
        output,
        width: u32_at(16)?,
        height: u32_at(20)?,
        stride0: u32_at(24)?,
        stride1: u32_at(28)?,
        data: message.slice(PIXELS_HEADER_BYTES as usize..),
    })
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<PageMessage>();
    types.visit::<HostMessage>();
}

#[cfg(test)]
mod tests;
