//! The vocabulary of a viewer's lanes: how a lane selects samples, where its load stands and what the feed produces.

use std::{ops::RangeInclusive, sync::Arc};

use foundation::UserError;
use overlay_sync::MatchPolicy;
use scene::{FilterPolicy, TimeKind};
use scene_decoders::LoadWarning;

/// How a lane selects samples for frames, and whether it wants any; the part of a lane's settings the backend needs.
/// Session state of the viewer's socket.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct LanePolicy {
    /// Tolerance and sticky.
    pub matching: MatchPolicy,
    /// Whether the lane is shown; an unsubscribed lane gets no Frame Maps and its samples are not sent for it.
    pub subscribed: bool,
}

/// Where a lane's load stands.
#[derive(Clone, Debug, PartialEq)]
pub enum LaneStatus {
    /// The overlay file is being decoded.
    Loading,
    /// The overlay is ready to be sent.
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
    /// The source's revision; its items and the lane's maps carry it.
    pub data_revision: u32,
    /// The mean size of a Sample Item in bytes, 0 for an empty file.
    pub mean_sample_bytes: u32,
}

/// One Sample Item of one source, for the viewer.
#[derive(Clone, Debug, PartialEq)]
pub struct SampleItem {
    /// The viewer's id of the source.
    pub source: u16,
    /// The source revision the item belongs to.
    pub data_revision: u32,
    /// The sample's index in the file.
    pub sample: u64,
    /// The frame the item ranks by: of the frames in the data window that map to the sample, the most urgent.
    pub frame: u64,
    /// Identifies this send of the sample, so that handing back an older send does not undo a newer one. Not on the
    /// wire.
    pub serial: u64,
    /// The encoded item (`scene::serial::columns`).
    pub item: Arc<[u8]>,
}

/// One run of one lane's Frame Map: consecutive frames that never span two 256-frame chunks.
#[derive(Clone, Debug, PartialEq)]
pub struct MapRun {
    /// The lane's position in the entry's lanes.
    pub lane: u16,
    /// The lane policy revision the run was selected under.
    pub policy_revision: u32,
    /// The lane's source revision.
    pub data_revision: u32,
    /// The first frame of the run.
    pub first: u64,
    /// The sample of each frame, or the NONE and EMPTY cells of the playback wire.
    pub samples: Vec<u32>,
    /// How each sample matched (the `MATCH_*` bytes of the playback wire).
    pub kinds: Vec<u8>,
    /// The frame the run ranks by: the one nearest the playhead.
    pub frame: u64,
}

impl MapRun {
    /// The frames the run covers.
    pub fn frames(&self) -> RangeInclusive<u64> {
        self.first
            ..=self
                .first
                .saturating_add(self.samples.len().saturating_sub(1) as u64)
    }
}

/// What a viewer's overlays report.
#[derive(Clone, Debug, PartialEq)]
pub enum OverlayEvent {
    /// A lane's load changed.
    Status {
        /// The lane's position in the entry's lanes.
        lane: u16,
        /// Where the load stands.
        status: LaneStatus,
    },
    /// A policy was refused or a sample could not be read; the lane carries on. Each reason is reported once per
    /// load, at most 16 per lane.
    Problem {
        /// The lane's position in the entry's lanes.
        lane: u16,
        /// What went wrong, in words for the user.
        reason: String,
    },
    /// A Sample Item.
    Sample(SampleItem),
    /// A run of a Frame Map.
    Map(MapRun),
    /// The frames the viewer keeps Frame Map cells for from now on: the data window applied. Precedes every run
    /// selected for it.
    Window {
        /// The first frame.
        first: u64,
        /// The last frame, inclusive.
        last: u64,
    },
    /// The viewer no longer holds these samples (ascending); the backend stopped counting them as sent.
    Evict {
        /// The viewer's id of the source.
        source: u16,
        /// The source revision the samples belong to.
        data_revision: u32,
        /// The sample indices.
        samples: Vec<u32>,
    },
}
