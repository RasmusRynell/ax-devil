//! The keys of the samples written so far: the published ones in shared chunks, the rest waiting for a publication.

use foundation::MediaTime;
use overlay_sync::{KeyChunks, SampleKeys};
use scene::{SceneTime, TimeKind};

use crate::format::Keys;

/// Keys on one clock, ascending: those readers can see and those still pending.
pub(crate) enum KeyLog {
    Frames {
        published: KeyChunks<u64>,
        pending: Vec<u64>,
    },
    Times {
        published: KeyChunks<MediaTime>,
        pending: Vec<MediaTime>,
    },
}

impl KeyLog {
    pub(crate) fn new(kind: TimeKind) -> Self {
        match kind {
            TimeKind::Frame => Self::Frames {
                published: KeyChunks::new(),
                pending: Vec::new(),
            },
            TimeKind::Timestamp => Self::Times {
                published: KeyChunks::new(),
                pending: Vec::new(),
            },
        }
    }

    /// Adds the key of the next sample, which must be on this clock and above the last.
    pub(crate) fn push(&mut self, time: SceneTime) {
        match (self, time) {
            (Self::Frames { pending, .. }, SceneTime::Frame(frame)) => pending.push(frame),
            (Self::Times { pending, .. }, SceneTime::Timestamp(time)) => pending.push(time),
            _ => debug_assert!(false, "a key of the other clock"),
        }
    }

    /// Moves the pending keys into the published list and returns it.
    pub(crate) fn publish(&mut self) -> SampleKeys {
        match self {
            Self::Frames { published, pending } => {
                *published = published.appended(pending);
                pending.clear();
                SampleKeys::Frames(published.clone())
            }
            Self::Times { published, pending } => {
                *published = published.appended(pending);
                pending.clear();
                SampleKeys::Times(published.clone())
            }
        }
    }

    /// Every key, published and pending, for writing the index.
    pub(crate) fn all(&self) -> Keys {
        match self {
            Self::Frames { published, pending } => {
                Keys::Frames(published.iter().chain(pending.iter().copied()).collect())
            }
            Self::Times { published, pending } => Keys::Times(
                published
                    .iter()
                    .chain(pending.iter().copied())
                    .map(|time| time.0)
                    .collect(),
            ),
        }
    }
}
