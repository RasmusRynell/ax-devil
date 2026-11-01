//! Tests of the lane pipeline through real MOT files and ONVIF records, with catalogs and track loads that the
//! tests can fail or hold.

mod statuses;
mod views;
mod window;

use std::{
    fs,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        mpsc::{Sender, channel},
    },
    time::Duration,
};

use catalogs::CatalogId;
use foundation::{CancelToken, ErrorCode, FrameRate, FrameSize, Interrupt, Timeline, UserError};
use handlers::Handlers;
use playback::{Direction, Priority, Want};
use playlist_resolvers::OverlayRef;
use render_catalog::CompiledCatalog;
use scene_store::{Track, TrackRegistry};
use tempfile::TempDir;

use crate::{
    LaneOverlay, LaneStatus, OverlayEvent, OverlayService, ViewerOverlays,
    sources::{CatalogSource, TrackSource},
};

pub(super) const SIZE: FrameSize = FrameSize {
    width: 1920,
    height: 1080,
};

const MINIMAL: &str = include_str!("../../../../assets/catalogs/minimal.json");

/// Catalogs that compile the Minimal catalog for every id but `user:bad`, which fails, and `user:panic`, which
/// takes the worker down.
pub(super) struct Catalogs {
    minimal: Arc<CompiledCatalog>,
    pub(super) asked: Mutex<Vec<CatalogId>>,
}

impl CatalogSource for Catalogs {
    fn default_id(&self) -> CatalogId {
        CatalogId::BuiltIn("standard")
    }

    fn compiled(&self, id: &CatalogId) -> Result<Arc<CompiledCatalog>, UserError> {
        self.asked.lock().expect("asked").push(id.clone());
        assert!(*id != CatalogId::User("panic".into()), "the worker dies");
        if *id == CatalogId::User("bad".into()) {
            return Err(UserError::new(
                ErrorCode::CatalogInvalid,
                format!("The catalog is damaged: {id}"),
            ));
        }
        Ok(Arc::clone(&self.minimal))
    }
}

/// Tracks from a real registry; opening the file named `held` waits until the viewer is closed.
pub(super) struct Tracks {
    pub(super) registry: TrackRegistry,
    held: Option<&'static str>,
    cancelled: Mutex<Sender<()>>,
}

impl TrackSource for Tracks {
    fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        if overlay.path.file_name().and_then(|name| name.to_str()) == self.held {
            while !cancel.is_cancelled() {
                std::thread::sleep(Duration::from_millis(5));
            }
            // The receiver is gone when the test no longer waits.
            let _ = self.cancelled.lock().expect("sender").send(());
            return Err(Interrupt::Cancelled);
        }
        self.registry.open(overlay, frame_size, cancel)
    }
}

/// A service over files in a temporary folder.
pub(super) struct Env {
    dir: TempDir,
    pub(super) service: OverlayService,
    pub(super) tracks: Arc<Tracks>,
    pub(super) catalogs: Arc<Catalogs>,
    /// Receives once a held load has seen its cancellation.
    pub(super) cancelled: std::sync::mpsc::Receiver<()>,
}

impl Env {
    pub(super) fn new() -> Self {
        Self::holding(None)
    }

    /// An environment whose loads of the file `held` never finish until cancelled.
    pub(super) fn holding(held: Option<&'static str>) -> Self {
        let (sender, cancelled) = channel();
        let tracks = Arc::new(Tracks {
            registry: TrackRegistry::new(Handlers::built_in(), None),
            held,
            cancelled: Mutex::new(sender),
        });
        let catalogs = Arc::new(Catalogs {
            minimal: Arc::new(
                render_catalog::compile(MINIMAL).expect("the Minimal catalog compiles"),
            ),
            asked: Mutex::new(Vec::new()),
        });
        Self {
            dir: TempDir::new().expect("temp dir"),
            service: OverlayService::from_sources(tracks.clone(), catalogs.clone()),
            tracks,
            catalogs,
            cancelled,
        }
    }

    /// Writes `name` and returns the overlay of the handler `handler` reading it.
    pub(super) fn overlay(&self, name: &str, handler: &str, text: &str) -> OverlayRef {
        let path = self.dir.path().join(name);
        fs::write(&path, text).expect("write overlay");
        overlay_at(name, handler, path)
    }

    /// A MOT overlay with one box in each of `frames` (0-based).
    pub(super) fn mot(&self, name: &str, frames: impl IntoIterator<Item = u64>) -> OverlayRef {
        let text: String = frames
            .into_iter()
            .map(|frame| format!("{},1,100,100,50,80,0.9,-1,-1,-1\n", frame + 1))
            .collect();
        self.overlay(name, "MOT_FILE", &text)
    }

    pub(super) fn open(&self, lanes: Vec<Option<OverlayRef>>, frames: u64) -> ViewerOverlays {
        self.service.open_viewer(lanes, timeline(frames), SIZE)
    }
}

pub(super) fn overlay_at(label: &str, handler: &str, path: PathBuf) -> OverlayRef {
    OverlayRef {
        label: label.into(),
        path,
        handler: handler.into(),
        options: serde_json::Map::new(),
    }
}

/// `frames` frames at 30 fps.
pub(super) fn timeline(frames: u64) -> Timeline {
    let rate = FrameRate::new(30, 1).expect("rate");
    let times = (0..frames)
        .map(|index| rate.time_of(index).expect("time"))
        .collect();
    Timeline::new(times).expect("timeline")
}

pub(super) fn want(playhead: u64, behind: u32, ahead: u32) -> Want {
    Want {
        playhead,
        ahead,
        behind,
        direction: Direction::Forward,
        priority: Priority::Normal,
    }
}

/// The next event, failing the test when none comes.
pub(super) async fn next(viewer: &mut ViewerOverlays) -> OverlayEvent {
    tokio::time::timeout(Duration::from_secs(10), viewer.next_event())
        .await
        .expect("an event in time")
        .expect("the viewer is open")
}

/// Whether an event arrives soon.
pub(super) async fn quiet(viewer: &mut ViewerOverlays) -> bool {
    tokio::time::timeout(Duration::from_millis(250), viewer.next_event())
        .await
        .is_err()
}

/// Reads events until every lane of `lanes` has a Ready status.
pub(super) async fn until_ready(viewer: &mut ViewerOverlays, lanes: &[u16]) {
    let mut waiting = lanes.to_vec();
    while !waiting.is_empty() {
        if let OverlayEvent::Status {
            lane,
            status: LaneStatus::Ready(_),
        } = next(viewer).await
        {
            waiting.retain(|&other| other != lane);
        }
    }
}

/// The next `count` overlays; other events on the way are returned beside them.
pub(super) async fn overlays(
    viewer: &mut ViewerOverlays,
    count: usize,
) -> (Vec<LaneOverlay>, Vec<OverlayEvent>) {
    let (mut found, mut others) = (Vec::new(), Vec::new());
    while found.len() < count {
        match next(viewer).await {
            OverlayEvent::Overlay(overlay) => found.push(overlay),
            other => others.push(other),
        }
    }
    (found, others)
}

/// `(lane, frame)` of each overlay in arrival order.
pub(super) fn places(found: &[LaneOverlay]) -> Vec<(u16, u64)> {
    found
        .iter()
        .map(|overlay| (overlay.lane, overlay.frame.sequence_id))
        .collect()
}
