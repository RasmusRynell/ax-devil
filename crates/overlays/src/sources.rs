//! Where a viewer gets tracks from, as a seam so tests can slow, fail or break it.

use std::sync::Arc;

use foundation::{CancelToken, Interrupt, UserError};
use handler_api::OverlayRef;
use scene_store::{Admission, TrackRead, TrackRegistry};

/// Opens the track of an overlay file, for a caller that may start a decode or only use what is built; blocking.
pub(crate) trait TrackSource: Send + Sync {
    fn open(
        &self,
        overlay: &OverlayRef,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>>;
}

impl TrackSource for TrackRegistry {
    fn open(
        &self,
        overlay: &OverlayRef,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>> {
        let track: Arc<dyn TrackRead> = TrackRegistry::open_as(self, overlay, admission, cancel)?;
        Ok(track)
    }
}
