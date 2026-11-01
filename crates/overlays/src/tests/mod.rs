//! Tests of the data feed through real MOT files, with track loads that the tests can hold or break.

mod delivery;
mod statuses;

use std::{
    fs,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        mpsc::{Sender, channel},
    },
    time::Duration,
};

use foundation::{CancelToken, FrameRate, FrameSize, Interrupt, Timeline, UserError};
use handlers::Handlers;
use overlay_sync::MatchPolicy;
use playback::{Direction, Priority, Want};
use playlist_resolvers::OverlayRef;
use scene_store::{Track, TrackRegistry};
use tempfile::TempDir;

use crate::{
    LaneStatus, MapRun, OverlayEvent, OverlayService, SampleItem, ViewerOverlays,
    sources::TrackSource,
};

pub(super) const SIZE: FrameSize = FrameSize {
    width: 1920,
    height: 1080,
};

/// Tracks from a real registry; opening the file named `held` waits until the viewer is closed, and the file
/// named `panic` takes the source thread down.
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
        let name = overlay.path.file_name().and_then(|name| name.to_str());
        assert!(name != Some("panic.txt"), "the source thread dies");
        if name == self.held {
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
    /// Receives once a held load has seen its cancellation.
    pub(super) cancelled: std::sync::mpsc::Receiver<()>,
}

impl Env {
    pub(super) fn new() -> Self {
        Self::build(None, false)
    }

    /// An environment whose loads of the file `held` never finish until cancelled.
    pub(super) fn holding(held: &'static str) -> Self {
        Self::build(Some(held), false)
    }

    /// An environment whose scene artifacts are kept in a folder, so a test can damage one.
    pub(super) fn caching() -> Self {
        Self::build(None, true)
    }

    fn build(held: Option<&'static str>, cache: bool) -> Self {
        let dir = TempDir::new().expect("temp dir");
        let cache_dir = cache.then(|| dir.path().join("cache"));
        let (sender, cancelled) = channel();
        let tracks = Arc::new(Tracks {
            registry: TrackRegistry::new(Handlers::built_in(), cache_dir.as_deref()),
            held,
            cancelled: Mutex::new(sender),
        });
        Self {
            dir,
            service: OverlayService::from_sources(tracks.clone()),
            tracks,
            cancelled,
        }
    }

    /// The folder the files are written to.
    pub(super) fn path(&self, name: &str) -> PathBuf {
        self.dir.path().join(name)
    }

    /// Writes `name` and returns the overlay of the handler `handler` reading it.
    pub(super) fn overlay(&self, name: &str, handler: &str, text: &str) -> OverlayRef {
        let path = self.path(name);
        fs::write(&path, text).expect("write overlay");
        overlay_at(name, handler, path)
    }

    /// A MOT overlay with one box in each of `frames` (0-based); the box of frame `n` is `n + 1` wide, so every
    /// sample encodes differently.
    pub(super) fn mot(&self, name: &str, frames: impl IntoIterator<Item = u64>) -> OverlayRef {
        let text: String = frames
            .into_iter()
            .map(|frame| format!("{},1,100,100,{},80,0.9,-1,-1,-1\n", frame + 1, frame + 1))
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

/// A forward data window of `behind` frames before and `ahead` after `playhead`.
pub(super) fn want(playhead: u64, behind: u32, ahead: u32) -> Want {
    Want {
        playhead,
        ahead,
        behind,
        direction: Direction::Forward,
        priority: Priority::Normal,
    }
}

/// The default matching, subscribed.
pub(super) fn shown() -> (Option<MatchPolicy>, bool) {
    (Some(MatchPolicy::default()), true)
}

/// The next event with no frame waiting, failing the test when none comes.
pub(super) async fn next(viewer: &mut ViewerOverlays) -> OverlayEvent {
    tokio::time::timeout(Duration::from_secs(10), viewer.next_event(None))
        .await
        .expect("an event in time")
        .expect("the viewer is open")
}

/// Whether no event arrives soon.
pub(super) async fn quiet(viewer: &mut ViewerOverlays) -> bool {
    tokio::time::timeout(Duration::from_millis(250), viewer.next_event(None))
        .await
        .is_err()
}

/// Reads events until every source of `lanes` has a Ready status.
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

/// What the viewer sent, in order.
#[derive(Default)]
pub(super) struct Sent {
    pub(super) maps: Vec<MapRun>,
    pub(super) samples: Vec<SampleItem>,
    pub(super) problems: Vec<(u16, String)>,
    /// `"map"` or `"sample"` in arrival order.
    pub(super) order: Vec<&'static str>,
}

/// Reads events until nothing arrives for a while.
pub(super) async fn settle(viewer: &mut ViewerOverlays) -> Sent {
    let mut sent = Sent::default();
    while let Ok(Some(event)) =
        tokio::time::timeout(Duration::from_millis(400), viewer.next_event(None)).await
    {
        match event {
            OverlayEvent::Map(run) => {
                sent.order.push("map");
                sent.maps.push(run);
            }
            OverlayEvent::Sample(item) => {
                sent.order.push("sample");
                sent.samples.push(item);
            }
            OverlayEvent::Problem { lane, reason } => sent.problems.push((lane, reason)),
            OverlayEvent::Status { .. } => {}
        }
    }
    sent
}

/// The `(frame, sample)` cells of all runs of `lane`.
pub(super) fn cells(sent: &Sent, lane: u16) -> Vec<(u64, u32)> {
    let mut found = Vec::new();
    for run in sent.maps.iter().filter(|run| run.lane == lane) {
        for (offset, &cell) in run.samples.iter().enumerate() {
            found.push((run.first + offset as u64, cell));
        }
    }
    found.sort_unstable();
    found
}

/// The sample indices sent, in arrival order.
pub(super) fn sample_ids(sent: &Sent) -> Vec<u64> {
    sent.samples.iter().map(|item| item.sample).collect()
}
