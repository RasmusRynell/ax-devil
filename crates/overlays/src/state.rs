//! The state a viewer's overlays share between the caller, the worker and the loaders, and the rules that change it.
//!
//! Like playback's sources, nothing is queued without a bound: a want updates the window in place, and the worker
//! derives its next job from the state, so a slow consumer or a burst of wants cannot grow anything.

use std::{
    collections::{HashSet, VecDeque},
    ops::RangeInclusive,
    sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError},
};

use catalogs::CatalogId;
use foundation::{CancelToken, FrameId, FrameSize, Timeline, UserError};
use overlay_sync::{SampleKeys, Selection};
use playback::{Direction, FrameSet, Want};
use playlist_resolvers::OverlayRef;
use render_catalog::CompiledCatalog;
use scene_store::Track;
use tokio::sync::Notify;

use crate::{LaneOverlay, LaneReady, LaneStatus, LaneView, OverlayEvent};

/// Prepared overlays waiting for the consumer; the worker stops preparing while they are full.
pub(crate) const READY_CAPACITY: usize = 8;

/// Problem notices kept for a consumer that is slow to read them; older ones are dropped first.
const NOTICE_LIMIT: usize = 64;

/// Distinct problems one lane reports per view, so a catalog failing on every frame cannot flood the consumer.
const REPORTED_LIMIT: usize = 16;

/// What a viewer's threads share.
pub(crate) struct Shared {
    pub(crate) state: Mutex<State>,
    /// Wakes the worker: new window, view, load or space in `ready`, or close.
    pub(crate) work: Condvar,
    /// Wakes `next_event`: a notice, an overlay or close.
    pub(crate) wake: Notify,
    /// Cancelled on close; interrupts loads in progress.
    pub(crate) cancel: CancelToken,
    pub(crate) timeline: Timeline,
    pub(crate) frame_size: FrameSize,
}

impl Shared {
    pub(crate) fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Marks the viewer closed, cancels loads and wakes everyone.
    pub(crate) fn close(&self) {
        self.lock().closed = true;
        self.cancel.cancel();
        self.work.notify_all();
        self.wake.notify_waiters();
    }

    /// Closes a viewer that ended without being closed: every lane with an overlay that has not failed fails with
    /// `error`, so the consumer stops waiting for overlays that will not come.
    pub(crate) fn abort(&self, error: &UserError) {
        {
            let mut state = self.lock();
            if state.closed {
                return;
            }
            state.fail_unfinished(error);
        }
        self.close();
    }

    /// Runs `change` on the state, then wakes the worker and the consumer.
    pub(crate) fn update<T>(&self, change: impl FnOnce(&mut State) -> T) -> T {
        let result = change(&mut self.lock());
        self.work.notify_all();
        self.wake.notify_waiters();
        result
    }
}

pub(crate) struct State {
    pub(crate) closed: bool,
    want: Option<Want>,
    last_frame: u64,
    pub(crate) lanes: Vec<Lane>,
    /// Statuses and problems, delivered before any overlay.
    notices: VecDeque<OverlayEvent>,
    /// Prepared and not yet delivered; each is also in its lane's `claimed`.
    ready: Vec<LaneOverlay>,
}

/// Where a lane's track stands.
pub(crate) enum Load {
    /// The lane draws no overlay file.
    NoOverlay,
    Loading,
    Ready(Arc<Track>),
    Failed,
}

pub(crate) struct Lane {
    pub(crate) overlay: Option<OverlayRef>,
    pub(crate) load: Load,
    view: Arc<LaneView>,
    revision: u32,
    /// The last catalog that compiled.
    pub(crate) loaded: Option<(CatalogId, Arc<CompiledCatalog>)>,
    /// A catalog that failed to compile and is not tried again until another is asked for.
    rejected: Option<CatalogId>,
    /// Frames of the window prepared for this revision: delivered, or waiting in `ready`. Leaving the window
    /// forgets them.
    claimed: FrameSet,
    /// The problems already reported for this view.
    reported: HashSet<String>,
}

/// What the worker does next.
pub(crate) enum Task {
    Compile { lane: usize, id: CatalogId },
    Prepare(Job),
}

/// One lane frame to prepare, with everything it needs so the work runs outside the lock.
pub(crate) struct Job {
    pub(crate) lane: usize,
    pub(crate) revision: u32,
    pub(crate) frame: FrameId,
    pub(crate) track: Arc<Track>,
    pub(crate) catalog: Option<Arc<CompiledCatalog>>,
    pub(crate) view: Arc<LaneView>,
}

/// What preparing a job produced.
pub(crate) struct Outcome {
    pub(crate) selection: overlay_sync::Selection,
    pub(crate) drawing: drawing::DrawingInstructions,
    pub(crate) problems: Vec<String>,
}

/// The key of the sample a selection draws; 0 when it draws none.
fn sample_key(keys: &SampleKeys, selection: &Selection) -> i64 {
    let Selection::Sample { index, .. } = *selection else {
        return 0;
    };
    match keys {
        SampleKeys::Frames(frames) => frames
            .get(index)
            .map_or(0, |&frame| i64::try_from(frame).unwrap_or(i64::MAX)),
        SampleKeys::Times(times) => times.get(index).map_or(0, |time| time.as_us()),
    }
}

/// Lower is more urgent: the playhead, then the direction of travel nearest first, then the other side.
type Rank = (u8, u64);

fn rank(direction: Direction, playhead: u64, frame: u64) -> Rank {
    let distance = frame.abs_diff(playhead);
    if distance == 0 {
        (0, 0)
    } else if (frame > playhead) == (direction == Direction::Forward) {
        (1, distance)
    } else {
        (2, distance)
    }
}

impl State {
    /// A viewer with one lane per entry of `lanes`; each overlay starts loading.
    pub(crate) fn new(
        lanes: Vec<Option<OverlayRef>>,
        defaults: &LaneView,
        last_frame: u64,
    ) -> Self {
        let mut notices = VecDeque::new();
        let lanes = lanes
            .into_iter()
            .enumerate()
            .map(|(index, overlay)| {
                let load = if overlay.is_some() {
                    notices.push_back(OverlayEvent::Status {
                        lane: lane_id(index),
                        status: LaneStatus::Loading,
                    });
                    Load::Loading
                } else {
                    Load::NoOverlay
                };
                Lane {
                    overlay,
                    load,
                    view: Arc::new(defaults.clone()),
                    revision: 0,
                    loaded: None,
                    rejected: None,
                    claimed: FrameSet::new(),
                    reported: HashSet::new(),
                }
            })
            .collect();
        Self {
            closed: false,
            want: None,
            last_frame,
            lanes,
            notices,
            ready: Vec::new(),
        }
    }

    /// The want, with its playhead inside the video, and the frames it keeps.
    fn window(&self) -> Option<(Want, RangeInclusive<u64>)> {
        let want = Want {
            playhead: self.want?.playhead.min(self.last_frame),
            ..self.want?
        };
        let low = want.playhead.saturating_sub(u64::from(want.behind));
        let high = want
            .playhead
            .saturating_add(u64::from(want.ahead))
            .min(self.last_frame);
        Some((want, low..=high))
    }

    /// Applies a want: frames that left the window are forgotten, so they are sent again when they return.
    pub(crate) fn set_want(&mut self, want: Want) {
        self.want = Some(want);
        let Some((_, window)) = self.window() else {
            return;
        };
        for lane in &mut self.lanes {
            lane.claimed.retain_within(&window);
        }
        self.ready
            .retain(|overlay| window.contains(&overlay.frame.sequence_id));
    }

    /// Replaces a lane's view; the whole window is prepared again for the new revision.
    pub(crate) fn set_view(&mut self, lane: usize, revision: u32, view: LaneView) {
        let Some(slot) = self.lanes.get_mut(lane).filter(|l| l.overlay.is_some()) else {
            return;
        };
        if slot.view.catalog != view.catalog {
            slot.reported.clear();
            if slot.rejected.as_ref() != Some(&view.catalog) {
                slot.rejected = None;
            }
        }
        slot.view = Arc::new(view);
        slot.revision = revision;
        slot.claimed.clear();
        let lane = lane_id(lane);
        self.ready.retain(|overlay| overlay.lane != lane);
    }

    /// Fails every lane with an overlay that has not failed already.
    fn fail_unfinished(&mut self, error: &UserError) {
        for index in 0..self.lanes.len() {
            let lane = &mut self.lanes[index];
            if lane.overlay.is_some() && !matches!(lane.load, Load::Failed) {
                lane.load = Load::Failed;
                self.push_notice(OverlayEvent::Status {
                    lane: lane_id(index),
                    status: LaneStatus::Failed(error.clone()),
                });
            }
        }
    }

    /// Records how a lane's load ended; a load finishing after the viewer closed is not reported.
    pub(crate) fn finish_load(&mut self, lane: usize, result: Result<Arc<Track>, UserError>) {
        let Some(slot) = self.lanes.get_mut(lane).filter(|_| !self.closed) else {
            return;
        };
        let status = match result {
            Ok(track) => {
                let ready = LaneReady {
                    clock: track.keys().kind(),
                    filter: track.filter().clone(),
                    warnings: track.warnings().to_vec(),
                    samples: track.keys().len(),
                };
                slot.load = Load::Ready(track);
                LaneStatus::Ready(ready)
            }
            Err(error) => {
                slot.load = Load::Failed;
                LaneStatus::Failed(error)
            }
        };
        self.push_notice(OverlayEvent::Status {
            lane: lane_id(lane),
            status,
        });
    }

    /// Records the outcome of compiling `id` for a lane; a result for a catalog the lane no longer asks for is
    /// dropped (the library has cached it).
    pub(crate) fn finish_compile(
        &mut self,
        lane: usize,
        id: CatalogId,
        result: Result<Arc<CompiledCatalog>, foundation::UserError>,
    ) {
        let Some(slot) = self.lanes.get_mut(lane).filter(|l| l.view.catalog == id) else {
            return;
        };
        match result {
            Ok(catalog) => {
                slot.loaded = Some((id, catalog));
                slot.rejected = None;
            }
            Err(error) => {
                slot.rejected = Some(id);
                self.report(lane, describe(&error));
            }
        }
    }

    /// Commits a prepared frame if it still belongs: same revision, lane shown, frame in the window and unsent.
    pub(crate) fn finish_prepare(&mut self, job: &Job, outcome: Outcome) {
        let Some((_, window)) = self.window() else {
            return;
        };
        let sequence_id = job.frame.sequence_id;
        let Some(lane) = self.lanes.get_mut(job.lane) else {
            return;
        };
        if lane.revision != job.revision
            || !lane.view.visible
            || !window.contains(&sequence_id)
            || lane.claimed.contains(sequence_id)
        {
            return;
        }
        lane.claimed.insert(sequence_id);
        let overlay = LaneOverlay {
            lane: lane_id(job.lane),
            frame: job.frame,
            revision: job.revision,
            target: job.view.target,
            key: sample_key(job.track.keys(), &outcome.selection),
            selection: outcome.selection,
            drawing: Arc::new(outcome.drawing),
        };
        self.ready.push(overlay);
        for reason in outcome.problems {
            self.report(job.lane, reason);
        }
    }

    /// Tells the consumer about a problem unless this view already did.
    pub(crate) fn report(&mut self, lane: usize, reason: String) {
        let Some(slot) = self.lanes.get_mut(lane) else {
            return;
        };
        if slot.reported.len() >= REPORTED_LIMIT || !slot.reported.insert(reason.clone()) {
            return;
        }
        self.push_notice(OverlayEvent::Problem {
            lane: lane_id(lane),
            reason,
        });
    }

    fn push_notice(&mut self, event: OverlayEvent) {
        if self.notices.len() >= NOTICE_LIMIT
            && let Some(oldest) = self
                .notices
                .iter()
                .position(|notice| matches!(notice, OverlayEvent::Problem { .. }))
        {
            self.notices.remove(oldest);
        }
        self.notices.push_back(event);
    }

    /// The next event for the consumer: notices in order, then the prepared overlay nearest the playhead.
    pub(crate) fn pull(&mut self) -> Option<OverlayEvent> {
        if let Some(notice) = self.notices.pop_front() {
            return Some(notice);
        }
        let want = self.want?;
        let best = self
            .ready
            .iter()
            .enumerate()
            .min_by_key(|(_, overlay)| {
                (
                    rank(want.direction, want.playhead, overlay.frame.sequence_id),
                    overlay.lane,
                )
            })
            .map(|(index, _)| index)?;
        Some(OverlayEvent::Overlay(self.ready.remove(best)))
    }

    /// The most urgent work: a catalog to compile, else the unsent frame nearest the playhead across lanes.
    pub(crate) fn next_task(&self, timeline: &Timeline) -> Option<Task> {
        for (index, lane) in self.lanes.iter().enumerate() {
            if lane.overlay.is_some() && !matches!(lane.load, Load::Failed) && !lane.settled() {
                return Some(Task::Compile {
                    lane: index,
                    id: lane.view.catalog.clone(),
                });
            }
        }
        if self.ready.len() >= READY_CAPACITY {
            return None;
        }
        let (want, window) = self.window()?;
        let mut best: Option<(Rank, usize, u64)> = None;
        for (index, lane) in self.lanes.iter().enumerate() {
            if !matches!(lane.load, Load::Ready(_)) || !lane.view.visible || !lane.settled() {
                continue;
            }
            for gap in lane.claimed.gaps_in(window.clone()) {
                let frame = want.playhead.clamp(*gap.start(), *gap.end());
                let key = (rank(want.direction, want.playhead, frame), index, frame);
                if best.is_none_or(|current| key < current) {
                    best = Some(key);
                }
            }
        }
        let (_, index, frame) = best?;
        let lane = &self.lanes[index];
        let Load::Ready(track) = &lane.load else {
            return None;
        };
        Some(Task::Prepare(Job {
            lane: index,
            revision: lane.revision,
            frame: timeline.frame(frame)?,
            track: Arc::clone(track),
            catalog: lane.loaded.as_ref().map(|(_, catalog)| Arc::clone(catalog)),
            view: Arc::clone(&lane.view),
        }))
    }

    /// The catalog a lane draws with, for tests.
    #[cfg(test)]
    pub(crate) fn loaded_catalog(&self, lane: usize) -> Option<CatalogId> {
        self.lanes
            .get(lane)?
            .loaded
            .as_ref()
            .map(|(id, _)| id.clone())
    }
}

impl Lane {
    /// Whether the lane's catalog question is answered: the asked-for catalog compiled or was rejected.
    fn settled(&self) -> bool {
        self.loaded
            .as_ref()
            .is_some_and(|(id, _)| *id == self.view.catalog)
            || self.rejected.as_ref() == Some(&self.view.catalog)
    }
}

/// The wire index of lane `index`; lanes beyond `u16::MAX` are not opened.
pub(crate) fn lane_id(index: usize) -> u16 {
    u16::try_from(index).unwrap_or(u16::MAX)
}

/// An error as one line for the host: the reason and, when it is about a file or a setting, which one.
pub(crate) fn describe(error: &foundation::UserError) -> String {
    use foundation::Location;
    match &error.location {
        Some(Location::Host(path)) => format!("{} ({})", error.reason, path.as_path().display()),
        Some(Location::Field(key)) => format!("{} ({key})", error.reason),
        Some(Location::Item(_)) | None => error.reason.clone(),
    }
}
