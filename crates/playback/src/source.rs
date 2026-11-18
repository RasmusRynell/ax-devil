//! One open video: its cache, its viewers' wants and the job board the decode workers draw from.

use crate::plan::{plan, reads_on};
use crate::window::{prefetch_windows, send_window};
use crate::{DecoderFactory, FrameSet, SharedFrame, SourceCache, SourceSpec, Span, Want, lock};
use foundation::{CancelToken, FrameFailure, HostPath, UserError};
use std::collections::BTreeMap;
use std::ops::RangeInclusive;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use tokio::sync::Notify;

/// Decode workers per source, each with its own decoder.
pub const DECODE_WORKERS: usize = 2;

/// Frames the cache could not hold that wait for one viewer to take them. The slots belong to the viewer, so a
/// viewer that stops reading holds back nobody else.
pub(crate) const PARKED_SLOTS: usize = 2;

pub(crate) struct Source {
    pub(crate) spec: SourceSpec,
    pub(crate) factory: DecoderFactory,
    pub(crate) cache: SourceCache,
    pub(crate) state: Mutex<State>,
    /// Wakes decode workers: new assignment or close.
    pub(crate) workers_cv: Condvar,
    /// Wakes sessions: new frame, failure, want or close.
    pub(crate) wake: Notify,
    /// Mirrors `State::closed` for the per-frame check, which takes no lock.
    pub(crate) closed: AtomicBool,
    /// Cancelled when the source closes, which interrupts a decoder blocked in input.
    pub(crate) cancel: CancelToken,
    /// What playback's own errors name: the file, as the registry knows it.
    pub(crate) location: HostPath,
    /// The machine's cores, the budget `decode_threads` divides.
    cores: usize,
    on_close: Mutex<Option<Box<dyn FnOnce() + Send>>>,
    /// Test seam: runs right after the last viewer has left, outside the state lock.
    #[cfg(test)]
    pub(crate) after_last_leave: Mutex<Option<Box<dyn FnOnce() + Send>>>,
}

pub(crate) struct State {
    pub(crate) sessions: BTreeMap<u64, SessionState>,
    next_session: u64,
    /// Frames that failed to decode; each counts as held, so it is never decoded again.
    pub(crate) failed: BTreeMap<u64, FrameFailure>,
    /// Window frames the cache cannot hold and no viewer can take right now: nobody needs them, or the viewers that
    /// do have their slots full. The planner leaves them alone. A viewer's want, or a slot it frees, makes the frames
    /// it needs plannable again; a change of the cache share makes all of them so.
    pub(crate) unfit: FrameSet,
    /// Set when the source cannot decode at all, such as a file that was replaced. It ends every viewer's session.
    pub(crate) fatal: Option<UserError>,
    /// The most urgent spans first, as of the last replan.
    pub(crate) plan: Vec<Span>,
    pub(crate) slots: [Slot; DECODE_WORKERS],
    /// Playhead of the newest want, the centre of eviction.
    focus: u64,
    pub(crate) closed: bool,
}

pub(crate) struct SessionState {
    pub(crate) want: Option<Want>,
    /// Frames sent while inside the send window; leaving the window forgets them.
    pub(crate) sent: FrameSet,
    /// Frames the cache could not hold, waiting for this viewer: at most `PARKED_SLOTS`.
    pub(crate) parked: Vec<SharedFrame>,
    /// Whether the viewer has been told the source failed.
    pub(crate) told: bool,
}

pub(crate) struct Slot {
    /// Where this worker's decoder stands, as of its last span.
    pub(crate) position: Option<u64>,
    pub(crate) claim: Option<Claim>,
}

pub(crate) struct Claim {
    pub(crate) span: Span,
    pub(crate) running: bool,
    pub(crate) shared: Arc<ClaimShared>,
}

/// What a running span reads between frames, without a lock.
pub(crate) struct ClaimShared {
    abandoned: AtomicBool,
    start: AtomicU64,
    end: AtomicU64,
}

impl ClaimShared {
    fn new(span: &Span) -> Self {
        let shared = Self {
            abandoned: AtomicBool::new(false),
            start: AtomicU64::new(0),
            end: AtomicU64::new(0),
        };
        shared.update(span);
        shared
    }

    fn update(&self, span: &Span) {
        self.start.store(*span.keep.start(), Ordering::Release);
        self.end.store(*span.keep.end(), Ordering::Release);
    }

    pub(crate) fn abandon(&self) {
        self.abandoned.store(true, Ordering::Release);
    }

    pub(crate) fn is_abandoned(&self) -> bool {
        self.abandoned.load(Ordering::Acquire)
    }

    pub(crate) fn start(&self) -> u64 {
        self.start.load(Ordering::Acquire)
    }

    pub(crate) fn end(&self) -> u64 {
        self.end.load(Ordering::Acquire)
    }

    pub(crate) fn range(&self) -> RangeInclusive<u64> {
        self.start()..=self.end()
    }
}

impl Source {
    pub(crate) fn new(
        spec: SourceSpec,
        factory: DecoderFactory,
        cache: SourceCache,
        location: HostPath,
    ) -> Arc<Self> {
        Arc::new(Self {
            spec,
            factory,
            cache,
            state: Mutex::new(State {
                sessions: BTreeMap::new(),
                next_session: 0,
                failed: BTreeMap::new(),
                unfit: FrameSet::new(),
                fatal: None,
                plan: Vec::new(),
                slots: std::array::from_fn(|_| Slot {
                    position: Some(0),
                    claim: None,
                }),
                focus: 0,
                closed: false,
            }),
            workers_cv: Condvar::new(),
            wake: Notify::new(),
            closed: AtomicBool::new(false),
            cancel: CancelToken::new(),
            location,
            cores: std::thread::available_parallelism().map_or(2, std::num::NonZero::get),
            on_close: Mutex::new(None),
            #[cfg(test)]
            after_last_leave: Mutex::new(None),
        })
    }

    pub(crate) fn set_on_close(&self, callback: Box<dyn FnOnce() + Send>) {
        *lock(&self.on_close) = Some(callback);
    }

    pub(crate) fn cores(&self) -> usize {
        self.cores
    }

    pub(crate) fn last(&self) -> u64 {
        self.spec.timeline.last()
    }

    /// Adds a viewer; `None` once the source is closed.
    pub(crate) fn add_session(&self) -> Option<u64> {
        let mut state = lock(&self.state);
        if state.closed {
            return None;
        }
        let id = state.next_session;
        state.next_session += 1;
        state.sessions.insert(
            id,
            SessionState {
                want: None,
                sent: FrameSet::new(),
                parked: Vec::new(),
                told: false,
            },
        );
        Some(id)
    }

    /// Applies a viewer's want: its sent set and parked frames forget what left the window, and the frames it now
    /// needs become plannable again. Then the work is replanned.
    pub(crate) fn want(&self, session: u64, want: Want) {
        let mut state = lock(&self.state);
        if state.closed {
            return;
        }
        let want = Want {
            playhead: want.playhead.min(self.last()),
            ..want
        };
        let window = send_window(&want, self.last());
        let Some(viewer) = state.sessions.get_mut(&session) else {
            return;
        };
        viewer.sent.retain_within(&window);
        viewer.want = Some(want);
        self.prune_parked(viewer);
        let demanded = self.demanded(viewer);
        state.focus = want.playhead;
        for range in &demanded {
            state.unfit.remove_range(range);
        }
        self.refresh(&mut state);
        drop(state);
        self.wake.notify_waiters();
    }

    /// Removes a viewer. The last one out closes the source in the same critical section, so a viewer joining
    /// afterwards finds it closed instead of being disconnected by a close that was already under way.
    pub(crate) fn remove_session(&self, session: u64) {
        let mut state = lock(&self.state);
        if state.sessions.remove(&session).is_none() {
            return;
        }
        if state.sessions.is_empty() {
            let closing = self.mark_closed(&mut state);
            drop(state);
            #[cfg(test)]
            if let Some(hook) = lock(&self.after_last_leave).take() {
                hook();
            }
            if closing {
                self.release();
            }
        } else {
            self.refresh(&mut state);
            drop(state);
            self.wake.notify_waiters();
        }
    }

    /// Recomputes the plan after the cache share changed.
    pub(crate) fn poke(&self) {
        let mut state = lock(&self.state);
        if state.closed {
            return;
        }
        state.unfit.clear();
        self.refresh(&mut state);
        drop(state);
        self.wake.notify_waiters();
    }

    /// Stops the workers at their next frame, frees the cache and ends every session. Never blocks on a worker.
    pub(crate) fn close(&self) {
        let closing = self.mark_closed(&mut lock(&self.state));
        if closing {
            self.release();
        }
    }

    /// Marks the source closed under the state lock; false when it already was.
    fn mark_closed(&self, state: &mut State) -> bool {
        if state.closed {
            return false;
        }
        state.closed = true;
        self.closed.store(true, Ordering::Release);
        state.plan.clear();
        state.sessions.clear();
        self.cancel.cancel();
        for claim in state.slots.iter().filter_map(|slot| slot.claim.as_ref()) {
            claim.shared.abandon();
        }
        true
    }

    /// What follows a close, outside the state lock: return the share, wake everyone, tell the registry.
    fn release(&self) {
        self.cache.close();
        self.workers_cv.notify_all();
        self.wake.notify_waiters();
        if let Some(callback) = lock(&self.on_close).take() {
            callback();
        }
    }

    /// Whether the viewer still has the frame in its window and has not been sent it.
    pub(crate) fn viewer_needs(&self, viewer: &SessionState, id: u64) -> bool {
        viewer.want.is_some_and(|want| {
            send_window(&want, self.last()).contains(&id) && !viewer.sent.contains(id)
        })
    }

    /// The runs of the viewer's send window it has not been sent.
    pub(crate) fn demanded(&self, viewer: &SessionState) -> Vec<RangeInclusive<u64>> {
        viewer
            .want
            .map(|want| viewer.sent.gaps_in(send_window(&want, self.last())))
            .unwrap_or_default()
    }

    /// Drops the viewer's parked frames it no longer needs; returns whether a slot was freed.
    pub(crate) fn prune_parked(&self, viewer: &mut SessionState) -> bool {
        let before = viewer.parked.len();
        let window = viewer.want.map(|want| send_window(&want, self.last()));
        let SessionState { parked, sent, .. } = viewer;
        parked.retain(|frame| {
            let id = frame.id().sequence_id;
            window.as_ref().is_some_and(|w| w.contains(&id)) && !sent.contains(id)
        });
        viewer.parked.len() < before
    }

    /// Ends every viewer's session with `error`: the source cannot decode any more. Decoding stops; each viewer
    /// is told once, in `next_delivery`, and then its session is over.
    pub(crate) fn fail_source(&self, error: UserError) {
        let mut state = lock(&self.state);
        if state.fatal.is_none() && !state.closed {
            state.fatal = Some(error);
            self.refresh(&mut state);
        }
        drop(state);
        self.wake.notify_waiters();
    }

    fn budget_frames(&self) -> usize {
        self.cache.budget_bytes() / self.spec.frame_bytes.max(1)
    }

    /// Frames the planner need not decode: cached, failed, parked for a viewer, unfit, or a playhead already
    /// delivered to every viewer on it.
    fn held(&self, state: &State) -> FrameSet {
        let mut held = self.cache.ranges();
        for &id in state.failed.keys() {
            held.insert(id);
        }
        for frame in state.sessions.values().flat_map(|viewer| &viewer.parked) {
            held.insert(frame.id().sequence_id);
        }
        for range in state.unfit.ranges() {
            held.insert_range(range.clone());
        }
        // A playhead frame every viewer on it has been sent needs no decoding, cached or not: when the cache cannot
        // hold it, it was delivered through a parking slot and decoding it again would deliver nothing.
        for want in state.sessions.values().filter_map(|viewer| viewer.want) {
            let playhead = want.playhead;
            let delivered = state
                .sessions
                .values()
                .filter(|viewer| viewer.want.is_some_and(|w| w.playhead == playhead))
                .all(|viewer| viewer.sent.contains(playhead));
            if delivered {
                held.insert(playhead);
            }
        }
        held
    }

    /// Replans, protects the windows in the cache, and hands the best spans to workers.
    pub(crate) fn refresh(&self, state: &mut State) {
        let wants: Vec<Want> = state
            .sessions
            .values()
            .filter_map(|viewer| viewer.want)
            .collect();
        let budget = self.budget_frames();
        let in_flight: Vec<Span> = state
            .slots
            .iter()
            .filter_map(|slot| slot.claim.as_ref().map(|claim| claim.span.clone()))
            .collect();
        let held = self.held(state);
        state.plan = if wants.is_empty() || state.fatal.is_some() {
            Vec::new()
        } else {
            plan(&self.spec, &wants, budget, &held, &in_flight)
        };
        let windows = prefetch_windows(&self.spec, &wants, budget);
        self.cache
            .protect(state.focus, if wants.is_empty() { &[] } else { &windows });
        self.assign(state);
    }

    /// Runs the best `DECODE_WORKERS` spans: running spans outside them are abandoned at their next frame, the
    /// rest keep going with their updated range, and idle workers take the others, preferring a worker whose
    /// decoder can read on.
    fn assign(&self, state: &mut State) {
        let top: Vec<Span> = state.plan.iter().take(DECODE_WORKERS).cloned().collect();
        for slot in &mut state.slots {
            let Some(claim) = slot.claim.as_mut() else {
                continue;
            };
            let wanted = if claim.shared.is_abandoned() {
                None
            } else {
                top.iter().find(|span| span.keyframe == claim.span.keyframe)
            };
            match wanted {
                Some(span) => {
                    claim.span = span.clone();
                    claim.shared.update(span);
                }
                None if claim.running => claim.shared.abandon(),
                None => slot.claim = None,
            }
        }
        for span in &top {
            let claimed = state.slots.iter().any(|slot| {
                slot.claim.as_ref().is_some_and(|claim| {
                    claim.span.keyframe == span.keyframe && !claim.shared.is_abandoned()
                })
            });
            if claimed {
                continue;
            }
            let idle: Vec<usize> = (0..DECODE_WORKERS)
                .filter(|&index| state.slots[index].claim.is_none())
                .collect();
            let first = *span.keep.start();
            let pick = idle
                .iter()
                .copied()
                .find(|&index| reads_on(&self.spec.keyframes, state.slots[index].position, first))
                .or_else(|| idle.first().copied());
            if let Some(index) = pick {
                state.slots[index].claim = Some(Claim {
                    shared: Arc::new(ClaimShared::new(span)),
                    span: span.clone(),
                    running: false,
                });
            }
        }
        self.workers_cv.notify_all();
    }
}
