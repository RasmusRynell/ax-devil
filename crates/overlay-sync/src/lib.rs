//! Overlay Lookup: which sample of an overlay file belongs to a video frame.
//!
//! Pure functions over sorted sample keys, the video's timeline and the user's policy. No I/O and no clock: the
//! same inputs always select the same sample, so the viewer and export agree. [`select`] is the one entry point; its
//! documentation states the rules.

mod alignment;
mod keys;
mod select;

use foundation::MediaTime;
use serde::{Deserialize, Serialize};

pub use alignment::{Alignment, Verdict, alignment};
pub use keys::{CHUNK_KEYS, KeyChunks};
pub use scene::MatchBasis;
pub use select::select;

/// The frame number a sample is found by and the sample it names.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FrameKey {
    /// The frame number.
    pub frame: u64,
    /// The sample's index in the [`SampleKeys`]' `times` order.
    pub sample: u32,
}

/// The keys of an overlay file's samples under each [`MatchBasis`]. Samples are numbered in time order; the
/// frame numbers of a time-keyed file are those of the records in file order, so they need not ascend with the samples.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SampleKeys {
    /// Frame numbers, ascending and unique: those of a frame-keyed file, whose samples are numbered by them, or the
    /// position in the file of each sample of a time-keyed one. `None` when the file cannot be matched by frame.
    pub frames: Option<KeyChunks<FrameKey>>,
    /// Timestamps on the video's Media Time clock, ascending and unique, by sample. `None` when the file has none.
    pub times: Option<KeyChunks<MediaTime>>,
}

impl SampleKeys {
    /// How many samples there are.
    pub fn len(&self) -> usize {
        match (&self.times, &self.frames) {
            (Some(keys), _) => keys.len(),
            (None, Some(keys)) => keys.len(),
            (None, None) => 0,
        }
    }

    /// Whether there are no samples.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// How far from a frame's time a time-keyed sample may be and still belong to it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
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

/// Keeping the last sample on screen while no sample matches. On by default, for 2.05 s.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Sticky {
    /// Whether samples are kept.
    pub enabled: bool,
    /// How old a kept sample may be; `None` is without limit.
    pub timeout: Option<MediaTime>,
}

impl Default for Sticky {
    fn default() -> Self {
        Self {
            enabled: true,
            timeout: Some(MediaTime(2_050_000)),
        }
    }
}

/// The user's choices for matching samples to frames.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct MatchPolicy {
    /// What records are matched to frames by; `None` is the decoder's default basis, which the host fills in once the
    /// file is open (until then, and for [`select`], it reads as [`MatchBasis::default`]).
    pub basis: Option<MatchBasis>,
    /// How near a time-keyed sample must be, under [`MatchBasis::Time`].
    pub tolerance: Tolerance,
    /// Whether and how long old samples stay.
    pub sticky: Sticky,
}

/// How a sample came to belong to a frame. The discriminant is the `MATCH_*` byte of the playback wire.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum MatchKind {
    /// The sample's key is the frame's.
    Exact = 0,
    /// An earlier sample within the tolerance.
    Tolerated = 1,
    /// An older sample kept by sticky.
    Retained = 2,
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
