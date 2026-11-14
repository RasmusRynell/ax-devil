//! An export job: a frame range of a video with the overlays of its lanes drawn on, written to a file.
//!
//! [`run`] decodes the frames, selects each lane's sample with [`overlays::cell`] (the Frame Map rule), has a
//! [`Renderer`] draw them and encodes the pixels with the source's frame times. The file is written beside the
//! destination and renamed into place only when everything succeeded.

mod destination;
mod render;
mod run;

use std::{ops::RangeInclusive, path::PathBuf, sync::Arc};

use foundation::{Cancelled, ErrorCode, UserError};
use media::{EncodeFormat, FrameIndex, VideoInput};
use scene::matching::MatchPolicy;

pub use render::{
    FrozenSource, IN_FLIGHT, LaneStyle, NewSample, Output, Pixels, RenderJob, RenderLane,
    RenderSetup, Renderer,
};
pub use run::run;

/// One lane an export draws.
#[derive(Clone)]
pub struct ExportLane {
    /// The lane's overlay file as the viewer held it; `None` draws the lane without data.
    pub source: Option<FrozenSource>,
    /// How the lane matches samples to frames.
    pub policy: MatchPolicy,
    /// The catalog document text, as served to the viewer.
    pub catalog: String,
    /// How the lane looks.
    pub style: LaneStyle,
}

/// Everything an export needs, frozen when it starts.
pub struct ExportSpec {
    /// The video to read the frames from.
    pub video: VideoInput,
    /// The index of `video`.
    pub index: Arc<FrameIndex>,
    /// The frames to export, by sequence id; one frame for a PNG.
    pub frames: RangeInclusive<u64>,
    /// The lanes, in the order they tile.
    pub lanes: Vec<ExportLane>,
    /// What to write.
    pub format: EncodeFormat,
    /// The file to write: absolute, in an existing folder, with the extension of `format`.
    pub destination: PathBuf,
    /// The overlay files the lanes read, which the destination must not be.
    pub inputs: Vec<PathBuf>,
}

/// Why an export did not produce a file.
#[derive(Debug, PartialEq, Eq)]
pub enum ExportError {
    /// The caller cancelled it.
    Cancelled,
    /// It could not be done, and why.
    Failed(UserError),
}

impl From<UserError> for ExportError {
    fn from(error: UserError) -> Self {
        Self::Failed(error)
    }
}

impl From<Cancelled> for ExportError {
    fn from(_: Cancelled) -> Self {
        Self::Cancelled
    }
}

/// An error that is the export's own, about nothing the user chose.
pub(crate) fn fail(reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::Internal, reason)
}
