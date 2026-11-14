//! Tests of the data feed through real MOT files, with track loads that the tests can hold or break.

mod delivery;
mod ingest;
mod inspect;
mod retention;
mod statuses;
mod viewer_model;

use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        mpsc::{Sender, channel},
    },
    time::Duration,
};

use foundation::{CancelToken, FrameRate, Interrupt, Timeline, UserError};
use handler_api::{Decoder, LaneSource, OverlayRef};
use handlers::Handlers;
use playback::{Direction, Priority, Want};
use scene::matching::MatchPolicy;
use scene_store::{TrackRead, TrackRegistry};
use tempfile::TempDir;

use crate::{
    LaneReady, LaneStatus, MapRun, OverlayEvent, OverlayService, SampleItem, ViewerOverlays,
    sources::TrackSource,
};

/// A tenth of the viewer's default 512 MiB frame budget.
pub(super) const SAMPLE_BUDGET: u64 = 53_687_091;

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
        cancel: &CancelToken,
    ) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>> {
        let name = overlay
            .file()
            .and_then(Path::file_name)
            .and_then(|name| name.to_str());
        assert!(name != Some("panic.txt"), "the source thread dies");
        if name == self.held {
            while !cancel.is_cancelled() {
                std::thread::sleep(Duration::from_millis(5));
            }
            // The receiver is gone when the test no longer waits.
            let _ = self.cancelled.lock().expect("sender").send(());
            return Err(Interrupt::Cancelled);
        }
        TrackSource::open(&self.registry, overlay, cancel)
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
        Self::with_handlers(Handlers::built_in(), held, cache)
    }

    fn with_handlers(handlers: Handlers, held: Option<&'static str>, cache: bool) -> Self {
        let dir = TempDir::new().expect("temp dir");
        let cache_dir = cache.then(|| dir.path().join("cache"));
        let (sender, cancelled) = channel();
        let tracks = Arc::new(Tracks {
            registry: TrackRegistry::new(handlers.decoder_lookup(), cache_dir.as_deref()),
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

    /// An environment that also reads `.paced` files with `decoder`.
    pub(super) fn with_decoder(decoder: Arc<dyn Decoder>) -> Self {
        Self::with_handlers(Handlers::built_in().with_decoders([decoder]), None, false)
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
        self.service
            .open_viewer(lanes, timeline(frames), SAMPLE_BUDGET)
    }
}

pub(super) fn overlay_at(label: &str, handler: &str, path: PathBuf) -> OverlayRef {
    // The MOT decoder needs the image size its boxes are measured in.
    let size = |key: &str| (handler == "MOT_FILE").then(|| (key.to_owned(), 1920.into()));
    OverlayRef {
        label: label.into(),
        slot: label.into(),
        source: LaneSource::File(path),
        handler: handler.into(),
        options: ["width", "height"].into_iter().filter_map(size).collect(),
        metadata: Default::default(),
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

/// Reads events until every source of `lanes` has a Ready status with its alignment, which follows the first one.
pub(super) async fn until_ready(viewer: &mut ViewerOverlays, lanes: &[u16]) {
    let mut waiting = lanes.to_vec();
    while !waiting.is_empty() {
        if let OverlayEvent::Status {
            lane,
            status: LaneStatus::Ready(ready),
        } = next(viewer).await
            && ready.alignment.is_some()
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
    /// The Ready statuses told.
    pub(super) readies: Vec<LaneReady>,
    /// The data windows told, as `(first, last)`.
    pub(super) windows: Vec<(u64, u64)>,
    /// The samples evicted, as `(source, samples)`.
    pub(super) evicts: Vec<(u16, Vec<u32>)>,
    /// `"map"` or `"sample"` in arrival order.
    pub(super) order: Vec<&'static str>,
}

impl Sent {
    /// Takes note of `event`.
    pub(super) fn record(&mut self, event: OverlayEvent) {
        match event {
            OverlayEvent::Map(run) => {
                self.order.push("map");
                self.maps.push(run);
            }
            OverlayEvent::Sample(item) => {
                self.order.push("sample");
                self.samples.push(item);
            }
            OverlayEvent::Problem { lane, reason } => self.problems.push((lane, reason)),
            OverlayEvent::Window { first, last } => self.windows.push((first, last)),
            OverlayEvent::Evict {
                source, samples, ..
            } => self.evicts.push((source, samples)),
            OverlayEvent::Status {
                status: LaneStatus::Ready(ready),
                ..
            } => self.readies.push(ready),
            OverlayEvent::Status { .. } => {}
        }
    }
}

/// Reads events until nothing arrives for a while.
pub(super) async fn settle(viewer: &mut ViewerOverlays) -> Sent {
    let mut sent = Sent::default();
    settle_into(viewer, &mut sent).await;
    sent
}

/// Like [`settle`], adding to `sent`.
pub(super) async fn settle_into(viewer: &mut ViewerOverlays, sent: &mut Sent) {
    while let Ok(Some(event)) =
        tokio::time::timeout(Duration::from_millis(400), viewer.next_event(None)).await
    {
        sent.record(event);
    }
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
