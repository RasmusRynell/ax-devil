//! The decode workers: take a span from the job board, decode it, cache what it produces.

use crate::decoder::decode_threads;
use crate::plan::reads_on;
use crate::source::{ClaimShared, PARKED_SLOTS, Source, State};
use crate::{FrameSet, SpanDecoder, lock};
use foundation::{ErrorCode, FrameFailure, UserError, VideoFrame};
use std::ops::RangeInclusive;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, PoisonError};
use tracing::{debug, warn};

/// One decode worker's own state: its index on the board and its decoder, opened on its first span.
pub(crate) struct Worker {
    pub(crate) index: usize,
    decoder: Option<Box<dyn SpanDecoder>>,
}

impl Worker {
    pub(crate) fn new(index: usize) -> Self {
        Self {
            index,
            decoder: None,
        }
    }
}

/// What one turn of a worker did.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Step {
    /// Decoded a span, fully or until it was abandoned.
    Ran,
    /// Nothing assigned to this worker.
    Idle,
    /// The source is closed.
    Closed,
}

impl Source {
    /// The thread body of a decode worker: runs spans until the source closes.
    pub(crate) fn run_worker(&self, index: usize) {
        let mut worker = Worker::new(index);
        loop {
            match self.step(&mut worker) {
                Step::Ran => {}
                Step::Closed => return,
                Step::Idle => self.wait_for_work(index),
            }
        }
    }

    fn wait_for_work(&self, index: usize) {
        let mut state = lock(&self.state);
        while !state.closed
            && !state.slots[index]
                .claim
                .as_ref()
                .is_some_and(|claim| !claim.running)
        {
            state = self
                .workers_cv
                .wait(state)
                .unwrap_or_else(PoisonError::into_inner);
        }
    }

    /// Runs the span assigned to this worker, if any, to its end; never waits for an assignment.
    pub(crate) fn step(&self, worker: &mut Worker) -> Step {
        let (keyframe, shared) = {
            let mut state = lock(&self.state);
            if state.closed {
                return Step::Closed;
            }
            let Some(claim) = state.slots[worker.index].claim.as_mut() else {
                return Step::Idle;
            };
            if claim.running {
                return Step::Idle;
            }
            claim.running = true;
            (claim.span.keyframe, Arc::clone(&claim.shared))
        };
        // A want can widen or move the claim at any moment; failures are only ever assigned to the frames the span
        // had actually reached, which `covered` records.
        let mut covered = shared.range();
        let outcome = catch_unwind(AssertUnwindSafe(|| {
            self.decode_span(&shared, keyframe, worker, &mut covered);
        }));
        if outcome.is_err() {
            warn!("a video decoder panicked; it is replaced on the next span");
            worker.decoder = None;
            self.fail_range(
                covered,
                &UserError::new(
                    ErrorCode::DecodeFailed,
                    "the video decoder stopped unexpectedly",
                )
                .at(self.location.clone()),
            );
        }
        let position = worker.decoder.as_ref().map_or(Some(0), |d| d.position());
        let mut state = lock(&self.state);
        state.slots[worker.index].claim = None;
        state.slots[worker.index].position = position;
        if !state.closed {
            self.refresh(&mut state);
        }
        drop(state);
        self.wake.notify_waiters();
        Step::Ran
    }

    fn stopping(&self, shared: &ClaimShared) -> bool {
        self.closed.load(std::sync::atomic::Ordering::Acquire) || shared.is_abandoned()
    }

    /// Decodes one span. `covered` is the range of frames this run has taken responsibility for: it starts as the
    /// claim at the start and follows the range each frame was decoded under, so a want that changes the claim
    /// while the span ends never makes frames it did not attempt count as failures.
    fn decode_span(
        &self,
        shared: &ClaimShared,
        keyframe: u64,
        worker: &mut Worker,
        covered: &mut RangeInclusive<u64>,
    ) {
        if worker.decoder.is_none() {
            let threads = decode_threads(self.cores(), self.cache.sources_open());
            match (self.factory)(&self.cancel, threads) {
                Ok(decoder) => worker.decoder = Some(decoder),
                Err(error) => {
                    warn!(reason = %error.reason, "could not open a video decoder");
                    self.fail_source(error);
                    return;
                }
            }
        }
        let Some(decoder) = worker.decoder.as_mut() else {
            return;
        };
        if !reads_on(&self.spec.keyframes, decoder.position(), shared.start())
            && let Err(error) = decoder.seek_to_keyframe(keyframe)
        {
            self.fail_range(covered.clone(), &error);
            return;
        }
        let first = decoder.position().unwrap_or(0);
        let mut produced = FrameSet::new();
        loop {
            if self.stopping(shared) {
                return;
            }
            let (start, end) = (shared.start(), shared.end());
            *covered = start..=end;
            let Some(expected) = decoder.position() else {
                break;
            };
            if expected > end {
                break;
            }
            match decoder.next_frame() {
                Ok(Some(frame)) => {
                    let id = frame.id().sequence_id;
                    produced.insert(id);
                    if (start..=end).contains(&id) {
                        self.store(frame, shared);
                    }
                    if id >= end {
                        break;
                    }
                }
                Ok(None) => break,
                Err(failure) => {
                    let id = failure.sequence_id;
                    produced.insert(id);
                    if (start..=end).contains(&id) {
                        self.record_failure(id, &failure);
                    }
                    if decoder.position().is_none_or(|next| next > expected) {
                        continue;
                    }
                    // The decoder cannot get past this frame: the rest of the span fails with it.
                    self.fail_range(expected..=end, &failure.error);
                    return;
                }
            }
        }
        // Frames the decoder never produced, such as the tail of a truncated file, are failures.
        let wanted = (*covered.start()).max(first)..=*covered.end();
        for gap in produced.gaps_in(wanted) {
            self.fail_range(
                gap,
                &UserError::new(ErrorCode::DecodeFailed, "the file ends early or is damaged")
                    .at(self.location.clone()),
            );
        }
    }

    /// Caches a decoded frame. A frame the cache cannot hold, because it is larger than the share or because the
    /// protected frames of other viewers fill it, is parked for each viewer that needs it, in that viewer's own
    /// bounded slots. A worker never waits for a slot, so a viewer that stopped reading cannot stall decoding for the
    /// others.
    fn store(&self, frame: VideoFrame, shared: &ClaimShared) {
        let id = frame.id().sequence_id;
        if self.cache.contains(id) {
            return;
        }
        if self.cache.insert(frame.clone()) {
            self.wake.notify_waiters();
            return;
        }
        let mut state = lock(&self.state);
        if state.closed || shared.is_abandoned() {
            return;
        }
        // The frame goes to every viewer that needs it and has a free slot. A viewer whose slots are full is not
        // waited for: its frame stays unplanned until it frees a slot, and nobody else is held back by it.
        let mut placed = false;
        let mut starved = false;
        let State { sessions, .. } = &mut *state;
        for viewer in sessions.values_mut() {
            let parked = viewer.parked.iter().any(|held| held.id().sequence_id == id);
            if parked || !self.viewer_needs(viewer, id) {
                continue;
            }
            if viewer.parked.len() < PARKED_SLOTS {
                viewer.parked.push(frame.clone());
                placed = true;
            } else {
                starved = true;
            }
        }
        if starved || !placed {
            state.unfit.insert(id);
        }
        drop(state);
        if placed {
            self.wake.notify_waiters();
        }
    }

    fn record_failure(&self, id: u64, failure: &FrameFailure) {
        let mut state = lock(&self.state);
        if self.cache.contains(id) || state.failed.contains_key(&id) {
            return;
        }
        if state.failed.len().is_power_of_two() {
            debug!(frame = id, reason = %failure.error.reason, failed = state.failed.len() + 1, "frame failed to decode");
        }
        state.failed.insert(
            id,
            FrameFailure {
                sequence_id: id,
                error: failure.error.clone(),
            },
        );
        drop(state);
        self.wake.notify_waiters();
    }

    /// Marks every frame of `range` that is neither cached nor failed as failed with `error`.
    pub(crate) fn fail_range(&self, range: RangeInclusive<u64>, error: &UserError) {
        let end = (*range.end()).min(self.last());
        let mut state = lock(&self.state);
        fail_missing(&self.cache, &mut state, *range.start()..=end, error);
        drop(state);
        self.wake.notify_waiters();
    }
}

fn fail_missing(
    cache: &crate::SourceCache,
    state: &mut State,
    range: RangeInclusive<u64>,
    error: &UserError,
) {
    for id in range {
        if !cache.contains(id) && !state.failed.contains_key(&id) {
            state.failed.insert(
                id,
                FrameFailure {
                    sequence_id: id,
                    error: error.clone(),
                },
            );
        }
    }
}
