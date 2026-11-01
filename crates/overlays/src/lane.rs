//! The vocabulary of a viewer's lanes: how a lane is shown, where its load stands and what it produces.

use std::sync::Arc;

use catalogs::CatalogId;
use drawing::DrawingInstructions;
use foundation::{FrameId, UserError};
use overlay_sync::{MatchPolicy, Selection};
use scene::{FilterChoice, FilterPolicy, TimeKind};
use scene_decoders::LoadWarning;

/// The displayed size of a lane's image in CSS pixels, which catalog lengths in pixels are measured against.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TargetSize {
    /// Width in CSS pixels.
    pub width: u32,
    /// Height in CSS pixels.
    pub height: u32,
}

/// How one lane shows its overlay; session state of the viewer.
#[derive(Clone, Debug, PartialEq)]
pub struct LaneView {
    /// The catalog that draws the lane.
    pub catalog: CatalogId,
    /// The class filter toggles.
    pub filter: FilterChoice,
    /// How samples are matched to frames.
    pub policy: MatchPolicy,
    /// The lane's displayed image size.
    pub target: TargetSize,
    /// Whether the lane is shown; a hidden lane produces no overlays.
    pub visible: bool,
}

/// Where a lane's load stands.
#[derive(Clone, Debug, PartialEq)]
pub enum LaneStatus {
    /// The overlay file is being decoded.
    Loading,
    /// The overlay is ready to draw.
    Ready(LaneReady),
    /// The overlay could not be loaded.
    Failed(UserError),
}

/// What a loaded lane tells the viewer.
#[derive(Clone, Debug, PartialEq)]
pub struct LaneReady {
    /// The clock the file's samples are on.
    pub clock: TimeKind,
    /// The class filters the file's content calls for.
    pub filter: FilterPolicy,
    /// Problems the decoder reported.
    pub warnings: Vec<LoadWarning>,
    /// How many samples the file has.
    pub samples: usize,
}

/// The overlay of one lane for one video frame.
#[derive(Clone, Debug, PartialEq)]
pub struct LaneOverlay {
    /// The lane's position in the entry's overlays.
    pub lane: u16,
    /// The video frame the overlay belongs to.
    pub frame: FrameId,
    /// The view revision the overlay was made for.
    pub revision: u32,
    /// The target size the overlay was made for.
    pub target: TargetSize,
    /// Which sample was drawn.
    pub selection: Selection,
    /// The key of that sample on the lane's clock: its frame number, or its time in microseconds; 0 when no sample
    /// was drawn.
    pub key: i64,
    /// What to draw.
    pub drawing: Arc<DrawingInstructions>,
}

/// What a viewer's overlays report.
#[derive(Clone, Debug, PartialEq)]
pub enum OverlayEvent {
    /// A lane's load changed.
    Status {
        /// The lane's position in the entry's overlays.
        lane: u16,
        /// Where the load stands.
        status: LaneStatus,
    },
    /// A lane frame is ready.
    Overlay(LaneOverlay),
    /// A catalog failed to compile or to draw a frame; the lane keeps drawing with its last catalog. Each reason is
    /// reported once per view.
    Problem {
        /// The lane's position in the entry's overlays.
        lane: u16,
        /// What went wrong, in words for the user.
        reason: String,
    },
}
