//! The state a viewer's overlays share between the caller and the source threads, and the rules that change it.
//!
//! Like playback's sources, nothing is queued without a bound: a want updates the windows in place, a source thread
//! derives its next sample from them (`pull`), and the consumer takes at most four encoded items per source, so a
//! slow consumer or a burst of wants cannot grow anything. The rules that move the windows are in `data`, those that
//! pick what to send next in `pull`.

use std::{
    collections::{BTreeMap, HashSet, VecDeque},
    ops::RangeInclusive,
    sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError},
    time::Instant,
};

use foundation::{CancelToken, Timeline, UserError};
use playback::{FrameSet, Want};
use playlist_resolvers::OverlayRef;
use scene_decoders::LoadWarning;
use scene_store::{Track, TrackSnapshot};
use tokio::sync::Notify;

use crate::{LanePolicy, LaneStatus, OverlayEvent, SampleItem, data::LaneWindow, ingest::Aligned};

/// Encoded items a source keeps waiting for the consumer; its thread stops encoding while they are full.
pub(crate) const READY_PER_SOURCE: usize = 4;

/// Notices kept for a consumer that is slow to read them; older problems are dropped first.
const NOTICE_LIMIT: usize = 64;

/// Distinct problems one lane reports per load, so a file with many damaged samples cannot flood the consumer.
const REPORTED_LIMIT: usize = 16;

/// What a viewer's threads share.
pub(crate) struct Shared {
    pub(crate) state: Mutex<State>,
    /// Wakes the source threads: a new window, space in `ready`, a finished load, or close.
    pub(crate) work: Condvar,
    /// Wakes `next_event`: a notice, an item, a window change or close.
    pub(crate) wake: Notify,
    /// Cancelled on close; interrupts loads in progress.
    pub(crate) cancel: CancelToken,
    pub(crate) timeline: Timeline,
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

    /// Closes a viewer that ended without being closed: every source that has not failed fails with `error`, so the
    /// consumer stops waiting for items that will not come.
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

    /// Runs `change` on the state, then wakes the source threads and the consumer.
    pub(crate) fn update<T>(&self, change: impl FnOnce(&mut State) -> T) -> T {
        let result = change(&mut self.lock());
        self.work.notify_all();
        self.wake.notify_waiters();
        result
    }
}

pub(crate) struct State {
    pub(crate) closed: bool,
    pub(crate) want: Option<Want>,
    pub(crate) last_frame: u64,
    pub(crate) lanes: Vec<Lane>,
    pub(crate) sources: Vec<Source>,
    /// Statuses and problems, delivered before any item.
    pub(crate) notices: VecDeque<OverlayEvent>,
    /// The most bytes of Sample Items the viewer holds at once.
    pub(crate) budget: u64,
    /// The serial of the next Sample Item sent.
    pub(crate) next_serial: u64,
    /// The data window not yet told to the consumer; only the latest matters.
    pub(crate) window_notice: Option<RangeInclusive<u64>>,
    /// The data window of the last want.
    pub(crate) applied: Option<RangeInclusive<u64>>,
    /// The revision the next finished load gets; starts at 1.
    pub(crate) next_revision: u32,
}

/// Where a source's track stands.
pub(crate) enum Load {
    Loading,
    /// The track, and the snapshot of it the lanes' maps and the sample window were last made from.
    Ready(Arc<Track>, Arc<TrackSnapshot>),
    Failed,
}

pub(crate) struct Lane {
    /// The lane's source, or none for a plain lane.
    pub(crate) source: Option<usize>,
    pub(crate) policy: LanePolicy,
    /// The policy revision the client last sent.
    pub(crate) revision: u32,
    /// The map of every frame of the data window; empty while the lane has nothing to send.
    pub(crate) window: LaneWindow,
    /// Frames of the data window whose map was sent under this revision. Leaving the window forgets them.
    pub(crate) maps_sent: FrameSet,
    /// The problems already reported since the source loaded.
    pub(crate) reported: HashSet<String>,
    /// The alignment warning last computed for the lane's file and policy.
    pub(crate) aligned: Option<Aligned>,
    /// The alignment warning in the last status queued for the lane.
    pub(crate) told: Option<LoadWarning>,
}

/// One overlay file of a viewer, shared by every lane that draws it.
pub(crate) struct Source {
    pub(crate) overlay: OverlayRef,
    pub(crate) load: Load,
    pub(crate) data_revision: u32,
    /// The mean item size the lanes were told, measured when the revision began and again when the ingest ended.
    pub(crate) mean_sample_bytes: u32,
    /// When the lanes were last told the source's status.
    pub(crate) last_ready: Instant,
    /// The sample window: each sample the subscribed lanes need, with the frame it ranks by.
    pub(crate) window: BTreeMap<u64, u64>,
    /// Samples the viewer holds, as far as the backend counts: sent, and not told to drop.
    pub(crate) sent: BTreeMap<u64, Held>,
    /// Samples the viewer still holds that the backend has stopped counting; its `Evict` is not sent yet. A sample
    /// that returns to the window before then is counted again.
    pub(crate) evicting: BTreeMap<u64, Held>,
    /// Encoded and not yet sent; in none of `sent` and `evicting`.
    pub(crate) ready: Vec<(u64, Arc<[u8]>)>,
    /// Samples that were encoded and did not fit the budget, with their sizes. Their items are dropped so that the
    /// encoder goes on to later samples; it encodes one again when its size fits.
    pub(crate) blocked: BTreeMap<u64, u64>,
}

/// One send of a sample.
#[derive(Clone, Copy)]
pub(crate) struct Held {
    /// Identifies the send, so handing back an older one does not undo a newer one.
    pub(crate) serial: u64,
    /// The size of the item.
    pub(crate) bytes: u64,
}

impl State {
    /// A viewer with one lane per entry of `lanes`; lanes on the same file share a source, and each source starts
    /// loading.
    pub(crate) fn new(lanes: Vec<Option<OverlayRef>>, last_frame: u64, budget: u64) -> Self {
        let mut sources: Vec<Source> = Vec::new();
        let mut notices = VecDeque::new();
        let lanes = lanes
            .into_iter()
            .enumerate()
            .map(|(index, overlay)| {
                let source = overlay.map(|overlay| {
                    let known = sources.iter().position(|source| {
                        let held = &source.overlay;
                        held.path == overlay.path
                            && held.handler == overlay.handler
                            && held.options == overlay.options
                    });
                    notices.push_back(OverlayEvent::Status {
                        lane: lane_id(index),
                        status: LaneStatus::Loading,
                    });
                    known.unwrap_or_else(|| {
                        sources.push(Source {
                            overlay,
                            load: Load::Loading,
                            data_revision: 0,
                            mean_sample_bytes: 0,
                            last_ready: Instant::now(),
                            window: BTreeMap::new(),
                            sent: BTreeMap::new(),
                            evicting: BTreeMap::new(),
                            ready: Vec::new(),
                            blocked: BTreeMap::new(),
                        });
                        sources.len() - 1
                    })
                });
                Lane {
                    source,
                    policy: LanePolicy::default(),
                    revision: 0,
                    window: LaneWindow::default(),
                    maps_sent: FrameSet::new(),
                    reported: HashSet::new(),
                    aligned: None,
                    told: None,
                }
            })
            .collect();
        Self {
            closed: false,
            want: None,
            last_frame,
            lanes,
            sources,
            notices,
            budget,
            next_serial: 0,
            window_notice: None,
            applied: None,
            next_revision: 1,
        }
    }

    /// The lanes of `source`, with their wire ids.
    pub(crate) fn lanes_of(&self, source: usize) -> impl Iterator<Item = usize> + '_ {
        self.lanes
            .iter()
            .enumerate()
            .filter(move |(_, lane)| lane.source == Some(source))
            .map(|(index, _)| index)
    }

    /// Fails every source that has not failed already, and with it its lanes.
    fn fail_unfinished(&mut self, error: &UserError) {
        for source in 0..self.sources.len() {
            if !matches!(self.sources[source].load, Load::Failed) {
                self.fail(source, error);
            }
        }
    }

    pub(crate) fn fail(&mut self, source: usize, error: &UserError) {
        self.sources[source].load = Load::Failed;
        let slot = &mut self.sources[source];
        slot.window.clear();
        slot.sent.clear();
        slot.evicting.clear();
        slot.ready.clear();
        slot.blocked.clear();
        for lane in self.lanes_of(source).collect::<Vec<_>>() {
            self.lanes[lane].window = LaneWindow::default();
            self.push_notice(OverlayEvent::Status {
                lane: lane_id(lane),
                status: LaneStatus::Failed(error.clone()),
            });
        }
    }

    /// Records how a source's load ended; a load finishing after the viewer closed is not reported. A new track
    /// takes the next data revision and starts every set of its lanes from empty.
    pub(crate) fn finish_load(
        &mut self,
        source: usize,
        result: Result<(Arc<Track>, u32), UserError>,
        timeline: &Timeline,
    ) {
        if self.closed || source >= self.sources.len() {
            return;
        }
        match result {
            Ok((track, mean)) => {
                let snapshot = track.snapshot();
                self.begin_revision(source, track, snapshot, mean, timeline);
            }
            Err(error) => self.fail(source, &error),
        }
    }

    /// Commits an encoded sample if it still belongs: same revision, in the sample window and not sent. A problem
    /// that made it an empty item is reported to every lane of the source.
    pub(crate) fn finish_encode(
        &mut self,
        source: usize,
        data_revision: u32,
        sample: u64,
        item: Arc<[u8]>,
        problem: Option<String>,
    ) {
        let Some(slot) = self.sources.get_mut(source) else {
            return;
        };
        if slot.data_revision != data_revision
            || !slot.window.contains_key(&sample)
            || slot.sent.contains_key(&sample)
            || slot.evicting.contains_key(&sample)
            || slot.ready.iter().any(|(held, _)| *held == sample)
        {
            return;
        }
        slot.blocked.remove(&sample);
        slot.ready.push((sample, item));
        if let Some(reason) = problem {
            for lane in self.lanes_of(source).collect::<Vec<_>>() {
                self.report(lane, reason.clone());
            }
        }
    }

    /// Takes back an event the consumer did not deliver, so what the viewer still needs of it is offered again: a
    /// sample is encoded and sent again, a map run's frames are sent again. A sample counts only if it is still the
    /// send that was handed back (its serial), and an event of another revision is of no concern any more.
    pub(crate) fn undelivered(&mut self, event: &OverlayEvent) {
        match event {
            OverlayEvent::Sample(item) => {
                let Some(slot) = self.sources.get_mut(usize::from(item.source)) else {
                    return;
                };
                if slot.data_revision != item.data_revision {
                    return;
                }
                for held in [&mut slot.sent, &mut slot.evicting] {
                    if held
                        .get(&item.sample)
                        .is_some_and(|held| held.serial == item.serial)
                    {
                        held.remove(&item.sample);
                    }
                }
            }
            OverlayEvent::Map(run) => {
                let Some(lane) = self.lanes.get_mut(usize::from(run.lane)) else {
                    return;
                };
                let Some(source) = lane.source.filter(|_| lane.revision == run.policy_revision)
                else {
                    return;
                };
                let slot = &mut self.sources[source];
                if slot.data_revision != run.data_revision {
                    return;
                }
                lane.maps_sent.remove_range(&run.frames());
            }
            OverlayEvent::Status { .. }
            | OverlayEvent::Problem { .. }
            | OverlayEvent::Window { .. }
            | OverlayEvent::Evict { .. } => {}
        }
    }

    /// Tells the consumer about a problem unless this load already did.
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

    pub(crate) fn push_notice(&mut self, event: OverlayEvent) {
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

    pub(crate) fn pop_notice(&mut self) -> Option<OverlayEvent> {
        self.notices.pop_front()
    }

    /// The sample item of an encoded sample as the consumer gets it.
    pub(crate) fn sample_item(
        &self,
        source: usize,
        sample: u64,
        serial: u64,
        item: Arc<[u8]>,
    ) -> SampleItem {
        let slot = &self.sources[source];
        SampleItem {
            source: lane_id(source),
            data_revision: slot.data_revision,
            sample,
            frame: slot.window.get(&sample).copied().unwrap_or_default(),
            serial,
            item,
        }
    }
}

/// The wire index of lane or source `index`; lanes beyond `u16::MAX` are not opened.
pub(crate) fn lane_id(index: usize) -> u16 {
    u16::try_from(index).unwrap_or(u16::MAX)
}

/// An error as one line for the host: the reason and, when it is about a file or a setting, which one.
pub(crate) fn describe(error: &UserError) -> String {
    use foundation::Location;
    match &error.location {
        Some(Location::Host(path)) => format!("{} ({})", error.reason, path.as_path().display()),
        Some(Location::Field(key)) => format!("{} ({key})", error.reason),
        Some(Location::Item(_)) | None => error.reason.clone(),
    }
}
