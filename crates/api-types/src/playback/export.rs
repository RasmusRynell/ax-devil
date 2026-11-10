//! Wire types of an export job on the playback socket (`ClientMessage::Export`, `ServerMessage::Export`).

use crate::ErrorWire;
use ::export::LaneStyle;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// A catalog document as the backend served it to the viewer: the export draws with exactly that text.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct CatalogRefWire {
    /// The catalog id, such as `built-in:standard`.
    pub id: String,
    /// The revision of the document the viewer was served.
    pub revision: u32,
}

/// One lane an export draws.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct ExportLaneWire {
    /// The lane's position in `Opened.lanes`.
    pub lane: u16,
    /// The catalog the lane draws with.
    pub catalog: CatalogRefWire,
    /// How the lane looks.
    pub style: LaneStyle,
    /// The lane's policy revision; the backend refuses the export when it differs from its own.
    pub policy_revision: u32,
    /// The source revision the viewer holds, absent for a plain lane; the backend refuses the export when it differs.
    pub data_revision: Option<u32>,
}

/// How good the video is: H.264 constant quality, from the largest file to the smallest.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum ExportQualityWire {
    /// Lossless.
    Lossless,
    /// Visually lossless.
    High,
    /// The default.
    Balanced,
    /// The smallest file.
    Small,
}

/// What an export writes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "format")]
pub enum ExportFormatWire {
    /// An H.264 video in an MP4 file.
    Mp4 {
        /// The quality preset.
        quality: ExportQualityWire,
    },
    /// One still PNG; the range is a single frame.
    Png,
}

/// Where an export job stands.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "state")]
pub enum ExportStateWire {
    /// Frames are being written.
    Running {
        /// Frames written.
        #[ts(type = "number")]
        done: u64,
        /// Frames in the range.
        #[ts(type = "number")]
        total: u64,
    },
    /// The file is in place.
    Done,
    /// The job was cancelled and nothing was written.
    Cancelled,
    /// The job failed and nothing was written.
    Failed(ErrorWire),
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<CatalogRefWire>();
    types.visit::<ExportLaneWire>();
    types.visit::<ExportQualityWire>();
    types.visit::<ExportFormatWire>();
    types.visit::<ExportStateWire>();
}
