//! The service that opens a viewer's overlays.

use std::{sync::Arc, thread};

use catalogs::Library;
use foundation::{FrameSize, Timeline};
use playlist_resolvers::OverlayRef;
use scene_store::TrackRegistry;

use crate::{
    LaneView, TargetSize, ViewerOverlays,
    sources::{CatalogSource, TrackSource},
    state::{Shared, State, lane_id},
    worker,
};

/// Opens the overlays of viewers. Cheap to clone.
#[derive(Clone)]
pub struct OverlayService {
    tracks: Arc<dyn TrackSource>,
    catalogs: Arc<dyn CatalogSource>,
}

impl OverlayService {
    /// A service loading tracks through `tracks` and drawing with `catalogs`.
    pub fn new(tracks: TrackRegistry, catalogs: Library) -> Self {
        Self::from_sources(Arc::new(tracks), Arc::new(catalogs))
    }

    pub(crate) fn from_sources(
        tracks: Arc<dyn TrackSource>,
        catalogs: Arc<dyn CatalogSource>,
    ) -> Self {
        Self { tracks, catalogs }
    }

    /// The view every lane starts with; the one place lane defaults live. The target is a placeholder until the
    /// viewer reports its size.
    pub fn lane_defaults(&self) -> LaneView {
        LaneView {
            catalog: self.catalogs.default_id(),
            filter: scene::FilterChoice::default(),
            policy: overlay_sync::MatchPolicy::default(),
            target: TargetSize {
                width: 1280,
                height: 720,
            },
            visible: true,
        }
    }

    /// Starts loading every lane's track in the background; a lane without an overlay never gets a status.
    ///
    /// Each overlay loads on its own thread (loads share decoding through the track registry) and one worker thread
    /// compiles catalogs and prepares frames, so nothing here blocks the caller. Lanes past `u16::MAX` are not
    /// opened.
    pub fn open_viewer(
        &self,
        mut lanes: Vec<Option<OverlayRef>>,
        timeline: Timeline,
        frame_size: FrameSize,
    ) -> ViewerOverlays {
        lanes.truncate(usize::from(u16::MAX));
        let state = State::new(lanes, &self.lane_defaults(), timeline.last());
        let shared = Arc::new(Shared {
            state: std::sync::Mutex::new(state),
            work: std::sync::Condvar::new(),
            wake: tokio::sync::Notify::new(),
            cancel: foundation::CancelToken::new(),
            timeline,
            frame_size,
        });
        let mut threads = Vec::new();
        let catalogs = Arc::clone(&self.catalogs);
        let worker_shared = Arc::clone(&shared);
        match thread::Builder::new()
            .name("overlays-worker".into())
            .spawn(move || worker::run(&worker_shared, catalogs.as_ref()))
        {
            Ok(handle) => threads.push(handle),
            Err(error) => {
                tracing::error!("cannot start the overlay worker: {error}");
                shared.abort(&foundation::UserError::new(
                    "Cannot start the overlays",
                    error.to_string(),
                ));
            }
        }
        let overlays: Vec<(usize, OverlayRef)> = shared
            .lock()
            .lanes
            .iter()
            .enumerate()
            .filter_map(|(index, lane)| Some((index, lane.overlay.clone()?)))
            .collect();
        for (index, overlay) in overlays {
            let (loader_shared, tracks) = (Arc::clone(&shared), Arc::clone(&self.tracks));
            let spawned = thread::Builder::new()
                .name(format!("overlays-load-{}", lane_id(index)))
                .spawn(move || worker::load(&loader_shared, tracks.as_ref(), index, &overlay));
            match spawned {
                Ok(handle) => threads.push(handle),
                Err(error) => {
                    tracing::error!("cannot start an overlay loader: {error}");
                    shared.update(|state| {
                        state.finish_load(
                            index,
                            Err(foundation::UserError::new(
                                "Cannot start loading the overlay",
                                error.to_string(),
                            )),
                        );
                    });
                }
            }
        }
        ViewerOverlays::new(shared, threads)
    }
}
