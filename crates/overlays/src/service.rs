//! The service that opens a viewer's overlays.

use std::{sync::Arc, thread, time::Duration};

use foundation::{CancelToken, Interrupt, Timeline, UserError};
use handler_api::OverlayRef;
use scene_store::{Admission, Ingest, TrackRead, TrackRegistry};

use crate::{
    ViewerOverlays,
    sources::TrackSource,
    state::{Shared, State, lane_id},
    worker,
};

/// How often [`OverlayService::warm`] looks at whether the file is read.
const WARM_POLL: Duration = Duration::from_millis(50);

/// Opens the overlays of viewers. Cheap to clone.
#[derive(Clone)]
pub struct OverlayService {
    tracks: Arc<dyn TrackSource>,
}

impl OverlayService {
    /// A service loading tracks through `tracks`.
    pub fn new(tracks: TrackRegistry) -> Self {
        Self::from_sources(Arc::new(tracks))
    }

    pub(crate) fn from_sources(tracks: Arc<dyn TrackSource>) -> Self {
        Self { tracks }
    }

    /// Opens the track of `overlay` as `admission` allows, for a reader that is not a viewer: it may still be
    /// reading, see [`TrackRead::snapshot`].
    pub fn open_track(
        &self,
        overlay: &OverlayRef,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>> {
        self.tracks.open(overlay, admission, cancel)
    }

    /// Reads the overlay file into the cache folder's artifact, so that a viewer opened on it later finds the artifact
    /// instead of decoding the file. Blocks until the file is read; give up early by cancelling `cancel`, which
    /// stops the read when no viewer shares it. A file that cannot be read is an error, which a viewer opened on it
    /// reports itself.
    pub fn warm(&self, overlay: &OverlayRef, cancel: &CancelToken) -> Result<(), UserError> {
        let track = match self.tracks.open(overlay, Admission::Compute, cancel) {
            Ok(track) => track,
            Err(Interrupt::Cancelled) => return Ok(()),
            Err(Interrupt::Failed(error)) => return Err(error),
        };
        // The track is open once it can be read; the artifact is complete when its ingest ends.
        while matches!(track.snapshot().ingest, Ingest::Reading) {
            if cancel.is_cancelled() {
                break;
            }
            thread::sleep(WARM_POLL);
        }
        Ok(())
    }

    /// Starts loading every source in the background; a lane without an overlay never gets a status. Lanes on the
    /// same file (same path, handler and options) share one source, loaded and sent once.
    ///
    /// `sample_budget` is the most bytes of Sample Items the viewer holds at once: the viewer never has more sent, but
    /// one item larger than it alone.
    ///
    /// Each source loads, then encodes, on its own thread (loads share decoding through the track registry), so
    /// nothing here blocks the caller. Lanes past `u16::MAX` are not opened.
    ///
    /// `admission` is what the viewer's demand may start: a viewer that is only [`Reuse`](Admission::Reuse)d never
    /// runs plugin code, so a lane nobody has built fails as not built yet.
    pub fn open_viewer(
        &self,
        mut lanes: Vec<Option<OverlayRef>>,
        timeline: Timeline,
        sample_budget: u64,
        admission: Admission,
    ) -> ViewerOverlays {
        lanes.truncate(usize::from(u16::MAX));
        let state = State::new(lanes, timeline.last(), sample_budget);
        let lane_sources: Vec<Option<u16>> = state
            .lanes
            .iter()
            .map(|lane| lane.source.map(lane_id))
            .collect();
        let overlays: Vec<OverlayRef> = state
            .sources
            .iter()
            .map(|source| source.overlay.clone())
            .collect();
        let shared = Arc::new(Shared {
            state: std::sync::Mutex::new(state),
            work: std::sync::Condvar::new(),
            wake: tokio::sync::Notify::new(),
            cancel: foundation::CancelToken::new(),
            timeline,
        });
        let mut threads = Vec::new();
        for (index, overlay) in overlays.into_iter().enumerate() {
            let (thread_shared, tracks) = (Arc::clone(&shared), Arc::clone(&self.tracks));
            let spawned = thread::Builder::new()
                .name(format!("overlays-source-{}", lane_id(index)))
                .spawn(move || {
                    worker::run(&thread_shared, tracks.as_ref(), index, &overlay, admission)
                });
            match spawned {
                Ok(handle) => threads.push(handle),
                Err(error) => {
                    tracing::error!("cannot start an overlay source: {error}");
                    shared.update(|state| {
                        state.finish_load(
                            index,
                            Err(foundation::UserError::new(
                                foundation::ErrorCode::Internal,
                                format!("Cannot start loading the overlay: {error}"),
                            )),
                            &shared.timeline,
                        );
                    });
                }
            }
        }
        ViewerOverlays::new(shared, threads, lane_sources)
    }
}
