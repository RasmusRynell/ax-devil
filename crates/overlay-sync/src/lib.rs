//! Overlay Lookup: which sample of an overlay file belongs to a video frame.
//!
//! Pure functions over sorted sample keys, the video's timeline and the user's policy. No I/O and no clock: the
//! same inputs always select the same sample, so the viewer and export agree. [`select`] is the one entry point; its
//! documentation states the rules.

mod select;

use std::sync::Arc;

use foundation::MediaTime;
use scene::TimeKind;

pub use select::select;

/// The keys of an overlay file's samples, ascending and unique, on the clock the file uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SampleKeys {
    /// Frame numbers of the video.
    Frames(Arc<[u64]>),
    /// Timestamps on the video's Media Time clock.
    Times(Arc<[MediaTime]>),
}

impl SampleKeys {
    /// The clock the keys are on.
    pub fn kind(&self) -> TimeKind {
        match self {
            Self::Frames(_) => TimeKind::Frame,
            Self::Times(_) => TimeKind::Timestamp,
        }
    }

    /// How many samples there are.
    pub fn len(&self) -> usize {
        match self {
            Self::Frames(keys) => keys.len(),
            Self::Times(keys) => keys.len(),
        }
    }

    /// Whether there are no samples.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// How far from a frame's time a time-keyed sample may be and still belong to it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tolerance {
    /// Only a sample at exactly the frame's time.
    Exact,
    /// The latest sample before the frame, at most this far back.
    PreviousWithin(MediaTime),
}

impl Default for Tolerance {
    fn default() -> Self {
        Self::PreviousWithin(MediaTime(50_000))
    }
}

/// Keeping the last sample on screen while no sample matches.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Sticky {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be; `None` is without limit.
    pub timeout: Option<MediaTime>,
}

impl Default for Sticky {
    fn default() -> Self {
        Self {
            enabled: false,
            timeout: Some(MediaTime(2_050_000)),
        }
    }
}

/// The user's choices for matching samples to frames.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct MatchPolicy {
    /// How near a time-keyed sample must be.
    pub tolerance: Tolerance,
    /// Whether and how long old samples stay.
    pub sticky: Sticky,
}

/// How a sample came to belong to a frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MatchKind {
    /// The sample's key is the frame's.
    Exact,
    /// An earlier sample within the tolerance.
    Tolerated,
    /// An older sample kept by sticky.
    Retained,
}

/// What belongs to one frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Selection {
    /// Nothing to draw: no match and sticky is off, too old, or the frame is before the first sample.
    None,
    /// A dense frame-keyed file covers this frame with nothing.
    Empty,
    /// The sample at `index` in the keys.
    Sample {
        /// Position in the [`SampleKeys`].
        index: usize,
        /// How the sample matched.
        kind: MatchKind,
    },
}
