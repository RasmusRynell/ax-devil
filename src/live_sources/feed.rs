//! One camera pipeline: the connections, the tasks that read them, the live track the overlay builds, and what the
//! viewers of the pipeline are shown. Two viewers of the same camera share one [`Feed`].

use std::{
    collections::VecDeque,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicI64, AtomicUsize, Ordering},
    },
};

use foundation::{ErrorCode, FrameId, UserError, VideoFrame};
use futures_util::future::join_all;
use handler_api::{Decoder, Settings};
use overlay_sync::Mono;
use scene_store::{LiveTrack, TrackRead, TrackSnapshot};
use tokio::{
    sync::{Mutex, watch},
    task::JoinHandle,
    time::Instant,
};
use workspace::LiveOverlay;

use super::{
    bridge::{Sessions, sessions},
    connector::{Closer, Connector},
    overlay::OverlayTask,
    source::Spec,
    status::{Board, OverlayLink},
    video::VideoTask,
};

/// How many samples the overlay's live track keeps.
const TRACK_SAMPLES: usize = 1024;
/// How many gap markers a feed keeps for viewers that have not read them yet.
const GAPS_KEPT: usize = 32;

/// Time as the gate and the status read it: since the feed began, on tokio's clock, so a test that pauses the clock
/// decides every deadline.
#[derive(Clone, Copy)]
pub struct Clock {
    origin: Instant,
}

impl Clock {
    /// A clock that starts now.
    pub fn start() -> Self {
        Self {
            origin: Instant::now(),
        }
    }

    /// The time now.
    pub fn now(&self) -> Mono {
        Mono(self.origin.elapsed())
    }

    /// The instant `time` falls on.
    pub fn instant(&self, time: Mono) -> Instant {
        self.origin + time.0
    }
}

/// A unit the gate let out, with what the overlay knew when it did. Each viewer's lane selects from the snapshot, so
/// its own policy decides what it draws. The Events list advances with every unit, picture or not.
pub struct Released {
    /// The unit's place on the video clock: its capture time.
    pub id: FrameId,
    /// The decoded picture; `None` when the unit gave none (nobody is watching, or it could not be decoded).
    pub frame: Option<VideoFrame>,
    /// The overlay's track; `None` for a video without one.
    pub snapshot: Option<Arc<TrackSnapshot>>,
}

/// The samples a lane lost, newest last, for the Events list.
#[derive(Clone, Default)]
pub struct GapLog {
    next: u64,
    recent: VecDeque<(u64, i64)>,
}

impl GapLog {
    /// How many gaps there have been.
    pub fn count(&self) -> u64 {
        self.next
    }

    /// The times of the gaps from number `from` on, oldest first.
    pub fn since(&self, from: u64) -> Vec<i64> {
        self.recent
            .iter()
            .filter(|(number, _)| *number >= from)
            .map(|(_, time_us)| *time_us)
            .collect()
    }

    fn push(&mut self, time_us: i64) {
        self.recent.push_back((self.next, time_us));
        self.next += 1;
        while self.recent.len() > GAPS_KEPT {
            self.recent.pop_front();
        }
    }
}

/// What a feed's tasks and viewers share.
pub struct Shared {
    pub(super) clock: Clock,
    pub(super) board: Board,
    pub(super) frames: watch::Sender<Option<Arc<Released>>>,
    pub(super) gaps: watch::Sender<GapLog>,
    pub(super) connector: Arc<dyn Connector>,
    pub(super) camera: workspace::ResolvedCamera,
    pub(super) overlay: LiveOverlay,
    pub(super) track: Option<Arc<LiveTrack>>,
    /// The overlay task's way to start decoder sessions.
    pub(super) sessions: Option<Sessions>,
    /// Viewers that are not paused. With none, the video is read and dropped, not decoded.
    pub(super) watching: AtomicUsize,
    /// The capture time of the newest video unit, microseconds, or `i64::MIN` before the first: the video clock
    /// where an overlay document has no time of its own.
    video_us: AtomicI64,
}

impl Shared {
    /// A gap of the overlay began at `time_us` on the video clock.
    pub(super) fn gap(&self, time_us: i64) {
        self.gaps.send_modify(|log| log.push(time_us));
    }

    /// Notes the capture time of a video unit.
    pub(super) fn video_at(&self, time_us: i64) {
        self.video_us.store(time_us, Ordering::Release);
    }

    /// The capture time of the newest video unit, if there was one.
    pub(super) fn video_now(&self) -> Option<i64> {
        Some(self.video_us.load(Ordering::Acquire)).filter(|time| *time != i64::MIN)
    }

    /// The track as a reader.
    pub(super) fn track_read(&self) -> Option<Arc<dyn TrackRead>> {
        self.track.clone().map(|track| track as Arc<dyn TrackRead>)
    }
}

/// The tasks and connections of one attempt.
struct Run {
    stop: watch::Sender<bool>,
    tasks: Vec<JoinHandle<()>>,
    closers: Vec<Closer>,
}

impl Run {
    /// Stops the tasks, then closes every connection and waits for each.
    async fn end(self) {
        self.stop.send_replace(true);
        for task in self.tasks {
            let _ = task.await;
        }
        join_all(self.closers).await;
    }
}

/// A running pipeline. Opens its connections when made and holds them until [`close`](Self::close).
pub struct Feed {
    pub(super) shared: Arc<Shared>,
    pub(super) spec: Spec,
    /// `None` once closed.
    run: Mutex<Option<Run>>,
    /// Viewers attached; read and changed under the registry's lock.
    pub(super) viewers: AtomicUsize,
    closed: AtomicBool,
    /// Whether a retry is waiting for the run or reconnecting; a request meanwhile is already answered by it.
    retrying: AtomicBool,
}

impl Feed {
    /// Starts the pipeline of `spec` and opens its connections. `decoder` is the overlay's, when it has one. Needs a
    /// running Tokio runtime.
    pub fn start(
        spec: Spec,
        connector: Arc<dyn Connector>,
        decoder: Option<Arc<dyn Decoder>>,
    ) -> Result<Arc<Self>, UserError> {
        let clock = Clock::start();
        let link = match &spec.overlay {
            LiveOverlay::None => OverlayLink::None,
            LiveOverlay::Rtsp { .. } => OverlayLink::Embedded,
            LiveOverlay::Mqtt { .. } | LiveOverlay::DataHub { .. } => OverlayLink::Separate,
        };
        let (track, sessions) = match decoder {
            Some(decoder) => {
                let values = decoder.spec().options.validate(&Settings::new())?;
                let (sessions, feed) = sessions();
                let track = LiveTrack::start(decoder, values, Box::new(feed), TRACK_SAMPLES)
                    .map_err(|error| {
                        UserError::new(
                            ErrorCode::Internal,
                            format!("The live overlay could not start: {error}"),
                        )
                    })?;
                (Some(track), Some(sessions))
            }
            None => (None, None),
        };
        let shared = Arc::new(Shared {
            clock,
            board: Board::new(link, clock.now()),
            frames: watch::channel(None).0,
            gaps: watch::channel(GapLog::default()).0,
            connector,
            camera: spec.camera.clone(),
            overlay: spec.overlay.clone(),
            track,
            sessions,
            watching: AtomicUsize::new(0),
            video_us: AtomicI64::new(i64::MIN),
        });
        let run = Self::connect(&shared);
        Ok(Arc::new(Self {
            shared,
            spec,
            run: Mutex::new(Some(run)),
            viewers: AtomicUsize::new(0),
            closed: AtomicBool::new(false),
            retrying: AtomicBool::new(false),
        }))
    }

    /// Opens every connection of the pipeline and starts the tasks that read them.
    fn connect(shared: &Arc<Shared>) -> Run {
        let (stop, stopped) = watch::channel(false);
        let mut tasks = Vec::new();
        let mut closers = Vec::new();
        let (video, closer) = match &shared.overlay {
            LiveOverlay::Rtsp { .. } => shared.connector.video(&shared.camera, true),
            _ => shared.connector.video(&shared.camera, false),
        }
        .split();
        closers.push(closer);
        let embedded = video.docs;
        let separate = match &shared.overlay {
            LiveOverlay::Mqtt {
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
                ..
            } => {
                let login = (!broker_user.is_empty() || !broker_pass.is_empty()).then(|| {
                    workspace::Login {
                        user: broker_user.clone(),
                        password: broker_pass.clone(),
                    }
                });
                let broker = super::connector::Broker {
                    host: broker_host.clone(),
                    port: *broker_port,
                    login,
                };
                Some(shared.connector.mqtt(&shared.camera, &broker, source_key))
            }
            LiveOverlay::DataHub { topic, channel, .. } => {
                Some(shared.connector.datahub(&shared.camera, topic, *channel))
            }
            LiveOverlay::None | LiveOverlay::Rtsp { .. } => None,
        };
        tasks.push(tokio::spawn(
            VideoTask::new(
                Arc::clone(shared),
                video.video,
                video.status,
                stopped.clone(),
            )
            .run(),
        ));
        let overlay = match separate {
            Some(transport) => {
                let (inputs, closer) = transport.split();
                closers.push(closer);
                Some((inputs.docs, Some(inputs.status)))
            }
            None => embedded.map(|docs| (Some(docs), None)),
        };
        if let Some((docs, link)) = overlay {
            tasks.push(tokio::spawn(
                OverlayTask::new(Arc::clone(shared), docs, link, stopped).run(),
            ));
        }
        Run {
            stop,
            tasks,
            closers,
        }
    }

    /// Asks for [`retry`](Self::retry) in the background, unless one is already waiting or running: any number of
    /// requests while the old connections close leave one task, not one each. Needs a running Tokio runtime.
    pub fn request_retry(self: &Arc<Self>) {
        if self.retrying.swap(true, Ordering::AcqRel) {
            return;
        }
        let feed = Arc::clone(self);
        tokio::spawn(async move {
            feed.retry().await;
            feed.retrying.store(false, Ordering::Release);
        });
    }

    /// Reopens every connection when one has failed for good or the video is stalled, as the viewer's Retry offers;
    /// otherwise there is nothing to retry. A feed that was closed stays closed, and so does one whose last viewer has
    /// left: the pipeline is then being closed, and connecting again would configure a camera for nobody.
    async fn retry(&self) {
        let mut run = self.run.lock().await;
        if !self.owned() || !self.shared.board.needs_retry(self.shared.clock.now()) {
            return;
        }
        let Some(failed) = run.take() else {
            return;
        };
        failed.end().await;
        // Ending the connections can take several request timeouts; the viewers may have gone meanwhile.
        if !self.owned() {
            return;
        }
        self.shared.board.restart(self.shared.clock.now());
        *run = Some(Self::connect(&self.shared));
    }

    /// Whether a viewer still holds the feed and it was not closed.
    fn owned(&self) -> bool {
        self.viewers.load(Ordering::Acquire) > 0 && !self.closed.load(Ordering::Acquire)
    }

    /// Ends the pipeline and waits until every connection is closed, a camera's MQTT publisher included. A closer
    /// that finds another one at work waits for it, so none reports closed while a connection is still ending.
    pub async fn close(&self) {
        let mut run = self.run.lock().await;
        if let Some(run) = run.take() {
            run.end().await;
        }
        self.closed.store(true, Ordering::Release);
    }

    /// Whether [`close`](Self::close) has finished.
    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::Acquire)
    }
}
