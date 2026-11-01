//! The service that opens a viewer's overlays.

use std::{sync::Arc, thread};

use foundation::{FrameSize, Timeline};
use overlay_sync::MatchPolicy;
use playlist_resolvers::OverlayRef;
use scene_store::TrackRegistry;

use crate::{
    ViewerOverlays,
    sources::TrackSource,
    state::{Shared, State, lane_id},
    worker,
};

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

    /// How samples are matched to frames until a lane says otherwise; the one place the lane defaults of the
    /// backend live.
    pub fn lane_defaults(&self) -> MatchPolicy {
        MatchPolicy::default()
    }

    /// Starts loading every source in the background; a lane without an overlay never gets a status. Lanes on the
    /// same file (same path, handler and options) share one source, loaded and sent once.
    ///
    /// `sample_budget` is the most bytes of Sample Items the viewer holds at once: the viewer never has more sent, but
    /// one item larger than it alone.
    ///
    /// Each source loads, then encodes, on its own thread (loads share decoding through the track registry), so
    /// nothing here blocks the caller. Lanes past `u16::MAX` are not opened.
    pub fn open_viewer(
        &self,
        mut lanes: Vec<Option<OverlayRef>>,
        timeline: Timeline,
        frame_size: FrameSize,
        sample_budget: u64,
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
            frame_size,
        });
        let mut threads = Vec::new();
        for (index, overlay) in overlays.into_iter().enumerate() {
            let (thread_shared, tracks) = (Arc::clone(&shared), Arc::clone(&self.tracks));
            let spawned = thread::Builder::new()
                .name(format!("overlays-source-{}", lane_id(index)))
                .spawn(move || worker::run(&thread_shared, tracks.as_ref(), index, &overlay));
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
