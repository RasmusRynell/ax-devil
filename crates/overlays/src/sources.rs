//! Where a viewer gets tracks from, as a seam so tests can slow, fail or break it.

use std::sync::Arc;

use foundation::{CancelToken, FrameSize, Interrupt, UserError};
use playlist_resolvers::OverlayRef;
use scene_store::{Track, TrackRegistry};

/// Opens the track of an overlay file; blocking.
pub(crate) trait TrackSource: Send + Sync {
    fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>>;
}

impl TrackSource for TrackRegistry {
    fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        TrackRegistry::open(self, overlay, frame_size, cancel)
    }
}
