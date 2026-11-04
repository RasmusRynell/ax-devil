//! One window's view of a feed: the newest frame with the cells its lane selects, the feeds' status, and the gaps of the
//! overlay. A viewer that reads slowly is shown the newest frame, never a queue of old ones.

use std::{
    collections::VecDeque,
    sync::{Arc, atomic::Ordering},
};

use api_types::live::LiveStatusWire;
use foundation::{MediaTime, VideoFrame};
use overlay_sync::{MatchBasis, MatchPolicy};
use overlays::{LanePolicy, LaneReady, LiveEvents, LiveLane, LiveOut};
use scene_store::{Ingest, TrackRead, TrackSnapshot};
use tokio::sync::watch;

use super::{
    feed::{Feed, GapLog, Released},
    source::Inner,
};

/// What a viewer is told next.
pub enum Update {
    /// A frame and, for a video with an overlay, what the lane draws on it.
    Frame(Box<Shown>),
    /// The overlay's events up to a unit that gave this viewer no picture: it is paused or the unit could not be decoded.
    Events(Box<LiveEvents>),
    /// A feed's status changed.
    Status(LiveStatusWire),
    /// The overlay lost samples from `time_us` on the video clock, microseconds.
    Gap {
        /// When the loss began.
        time_us: i64,
    },
}

/// A released frame as one viewer's lane sees it.
pub struct Shown {
    /// The picture, timed by the camera's capture time.
    pub frame: VideoFrame,
    /// The lane's cell, the Sample Items the viewer lacks for it and the events it made visible; `None` when the video
    /// has no overlay.
    pub out: Option<LiveOut>,
}

/// A window's attachment to a feed. Dropping it detaches, and the last viewer out ends the feed's connections in the
/// background; [`LiveSources::shutdown`](super::source::LiveSources::shutdown) waits for that.
pub struct LiveViewer {
    sources: Arc<Inner>,
    feed: Arc<Feed>,
    frames: watch::Receiver<Option<Arc<Released>>>,
    status: watch::Receiver<LiveStatusWire>,
    gaps: watch::Receiver<GapLog>,
    gaps_seen: u64,
    gaps_waiting: VecDeque<i64>,
    lane: Option<LiveLane>,
    policy: LanePolicy,
    paused: bool,
    /// The snapshot and sequence id of the frame shown last, which Sample Items the viewer holds are read from.
    shown: Option<(Arc<TrackSnapshot>, u64)>,
}

impl LiveViewer {
    /// Attaches to `feed`, counted by the caller. The lane starts unsubscribed with `matching` and policy revision 0.
    pub(super) fn attach(sources: Arc<Inner>, feed: Arc<Feed>, matching: MatchPolicy) -> Self {
        let shared = &feed.shared;
        shared.watching.fetch_add(1, Ordering::AcqRel);
        let gaps = shared.gaps.subscribe();
        let gaps_seen = gaps.borrow().count();
        Self {
            frames: shared.frames.subscribe(),
            status: shared.board.subscribe(),
            lane: shared
                .track
                .as_ref()
                .map(|track| LiveLane::new(track.clone(), &track.snapshot_with_open(), 0, 0, 0)),
            gaps,
            gaps_seen,
            gaps_waiting: VecDeque::new(),
            policy: LanePolicy {
                matching,
                subscribed: false,
            },
            paused: false,
            shown: None,
            sources,
            feed,
        }
    }

    /// Whether the video has an overlay, and so a lane to draw it.
    pub fn has_overlay(&self) -> bool {
        self.lane.is_some()
    }

    /// What the lane tells the viewer about its overlay: the live track as it stands now, which changes with the track's
    /// numbering and the decoder's class filters. `None` without an overlay.
    pub fn lane_ready(&self) -> Option<LaneReady> {
        let lane = self.lane.as_ref()?;
        let track = self.feed.shared.track_read()?;
        let snapshot = track.snapshot();
        Some(LaneReady {
            bases: vec![MatchBasis::Time],
            default_basis: MatchBasis::Time,
            filter: track.filter().clone(),
            warnings: snapshot.warnings.to_vec(),
            alignment: None,
            samples: snapshot.keys.len(),
            data_revision: lane.data_revision(),
            mean_sample_bytes: 0,
            ingest: Ingest::Reading,
        })
    }

    /// What an inspection of `sample` of source 0 under `data_revision` reads: the track, a snapshot that numbers the
    /// sample as the viewer holds it, and the frame it was last shown with. `None` for a sample the lane never sent or has evicted.
    pub fn inspect_target(
        &self,
        source: u16,
        data_revision: u32,
        sample: u32,
    ) -> Option<(Arc<dyn TrackRead>, Arc<TrackSnapshot>, u64)> {
        let lane = self.lane.as_ref()?;
        let (snapshot, frame) = self.shown.as_ref()?;
        let held = source == 0 && lane.data_revision() == data_revision && lane.holds(sample);
        if !held {
            return None;
        }
        Some((self.feed.shared.track_read()?, Arc::clone(snapshot), *frame))
    }

    /// Where the feeds stand now.
    pub fn status(&self) -> LiveStatusWire {
        self.status.borrow().clone()
    }

    /// Changes how the lane selects samples, and whether it is shown. Frames shown from now on carry `revision`.
    pub fn set_policy(&mut self, revision: u32, matching: MatchPolicy, subscribed: bool) {
        self.policy = LanePolicy {
            matching,
            subscribed,
        };
        if let Some(lane) = &mut self.lane {
            lane.set_policy(revision, matching.sticky);
        }
    }

    /// Stops or resumes delivering frames to this viewer. The connection stays up, and so do the status, the gaps and
    /// the events. A viewer that resumes is shown the newest frame, not the ones it missed. When every viewer is
    /// paused the video is read but not decoded.
    pub fn pause(&mut self, paused: bool) {
        if paused == self.paused {
            return;
        }
        self.paused = paused;
        let watching = &self.feed.shared.watching;
        if paused {
            watching.fetch_sub(1, Ordering::AcqRel);
        } else {
            watching.fetch_add(1, Ordering::AcqRel);
        }
    }

    /// Reopens every connection of a feed that failed or whose video stalled, for every viewer of it, and resumes this
    /// one. Does nothing to a feed that is up. The feed does it in the background, and not at all once its last viewer
    /// has gone, so the caller is never held up by a camera that is slow to give back its connections.
    pub fn retry(&mut self) {
        self.pause(false);
        self.feed.request_retry();
    }

    /// The next thing to tell the viewer; `None` only when the feed is gone. Cancel-safe.
    pub async fn next(&mut self) -> Option<Update> {
        loop {
            if let Some(time_us) = self.gaps_waiting.pop_front() {
                return Some(Update::Gap { time_us });
            }
            tokio::select! {
                changed = self.status.changed() => {
                    changed.ok()?;
                    return Some(Update::Status(self.status.borrow_and_update().clone()));
                }
                changed = self.gaps.changed() => {
                    changed.ok()?;
                    let log = self.gaps.borrow_and_update();
                    self.gaps_waiting.extend(log.since(self.gaps_seen));
                    self.gaps_seen = log.count();
                }
                changed = self.frames.changed() => {
                    changed.ok()?;
                    let released = self.frames.borrow_and_update().clone();
                    if let Some(update) = released.and_then(|released| self.show(&released)) {
                        return Some(update);
                    }
                }
            }
        }
    }

    /// What a released unit means to this viewer: the picture with its lane's cell, or, when it is paused or the unit
    /// gave no picture, only the events the unit made visible.
    fn show(&mut self, released: &Released) -> Option<Update> {
        let picture = released.frame.clone().filter(|_| !self.paused);
        let (Some(lane), Some(snapshot)) = (&mut self.lane, &released.snapshot) else {
            return picture.map(|frame| Update::Frame(Box::new(Shown { frame, out: None })));
        };
        let Some(frame) = picture else {
            let events = lane.unreported(released.id, snapshot);
            self.gaps_waiting
                .extend(lane.take_gap().map(MediaTime::as_us));
            return Some(Update::Events(Box::new(events?)));
        };
        // A decoder that holds pictures back answers a unit with an earlier picture: the lane matches that one's own
        // id, not the unit's.
        let id = frame.id();
        self.shown = Some((Arc::clone(snapshot), id.sequence_id));
        let out = Some(lane.release(id, snapshot, &self.policy));
        self.gaps_waiting
            .extend(lane.take_gap().map(MediaTime::as_us));
        Some(Update::Frame(Box::new(Shown { frame, out })))
    }
}

impl Drop for LiveViewer {
    fn drop(&mut self) {
        if !self.paused {
            self.feed.shared.watching.fetch_sub(1, Ordering::AcqRel);
        }
        self.sources.release(&self.feed);
    }
}
