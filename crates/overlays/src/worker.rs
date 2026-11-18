//! The thread of a source: it loads the track, then encodes the samples the viewer's window needs.

use std::{sync::Arc, thread};

use foundation::{ErrorCode, Interrupt, UserError};
use handler_api::OverlayRef;
use scene_store::{TrackRead, TrackSnapshot};

use crate::{
    sources::TrackSource,
    state::{Load, Shared, describe},
};

/// How many evenly spaced samples the load encodes to learn the mean item size (and to warm the track's cache).
const MEAN_SAMPLES: usize = 8;

/// Closes the viewer when its thread panics, so a consumer waiting in `next_event` is released. A viewer still open
/// then fails its sources first: the thread died, and their items will not come.
struct CloseOnPanic<'a>(&'a Shared);

impl Drop for CloseOnPanic<'_> {
    fn drop(&mut self) {
        if thread::panicking() {
            self.0.abort(&UserError::new(
                ErrorCode::Internal,
                "The overlays stopped unexpectedly",
            ));
            self.0.close();
        }
    }
}

/// Loads one source's track, records how it went, and then encodes samples until the viewer closes, taking up what the
/// track's ingest publishes meanwhile. A cancelled load records nothing.
pub(crate) fn run(
    shared: &Arc<Shared>,
    tracks: &dyn TrackSource,
    source: usize,
    overlay: &OverlayRef,
) {
    let _close = CloseOnPanic(shared);
    let opened = match tracks.open(overlay, &shared.cancel) {
        Ok(track) => track,
        Err(Interrupt::Cancelled) => return,
        Err(Interrupt::Failed(error)) => {
            shared.update(|state| state.finish_load(source, Err(error), &shared.timeline));
            return;
        }
    };
    // Registered before the load is recorded, so a publication in between is not missed: the load takes the
    // snapshot current when it is recorded, and a listener call for an older one finds nothing to do.
    let listener: Arc<dyn Fn() + Send + Sync> = {
        let (shared, track) = (Arc::clone(shared), Arc::clone(&opened));
        Arc::new(move || published(&shared, source, &*track))
    };
    opened.on_change(Arc::downgrade(&listener));
    let mean = mean_item_bytes(shared, &*opened);
    shared.update(|state| state.finish_load(source, Ok((opened.clone(), mean)), &shared.timeline));
    shared.align();
    encode(shared, source, &*opened);
}

/// The track published more samples or ended; runs on its ingest thread. The item size is measured again only when
/// the samples were renumbered or the ingest ended, not at each publication.
fn published(shared: &Shared, source: usize, track: &dyn TrackRead) {
    if shared.cancel.is_cancelled() {
        return;
    }
    let measure = shared.lock().remeasure(source, &track.snapshot());
    let mean = measure.then(|| mean_item_bytes(shared, track));
    shared.update(|state| state.track_changed(source, mean, &shared.timeline));
    shared.align();
}

/// The mean size of the items of up to `MEAN_SAMPLES` evenly spaced samples; samples that cannot be read do not
/// count.
fn mean_item_bytes(shared: &Shared, track: &dyn TrackRead) -> u32 {
    let count = track.snapshot().keys.len();
    if count == 0 {
        return 0;
    }
    let mut picked: Vec<usize> = (0..MEAN_SAMPLES)
        .map(|k| k * (count - 1) / (MEAN_SAMPLES - 1))
        .collect();
    picked.dedup();
    let mut total = 0_usize;
    let mut read = 0_usize;
    for index in picked {
        if shared.cancel.is_cancelled() {
            break;
        }
        if let Ok(item) = track.item(index) {
            total += item.len();
            read += 1;
        }
    }
    u32::try_from(total.checked_div(read).unwrap_or(0)).unwrap_or(u32::MAX)
}

/// The encoder loop of one source: the most urgent unclaimed sample of its window, until the viewer closes. A
/// sample that cannot be read or encoded still goes, as an empty item with a problem, so no lane waits for it. A
/// sample is read by the numbering of the snapshot it was claimed under, so one the track has since renumbered is
/// read as it was and dropped with its revision.
fn encode(shared: &Shared, source: usize, track: &dyn TrackRead) {
    while let Some((sample, data_revision, snapshot)) = next_sample(shared, source) {
        let read = usize::try_from(sample).map(|index| track.item_in(&snapshot, index));
        let (item, problem) = match read {
            Ok(Ok(item)) => (item, None),
            Ok(Err(error)) => (track.empty_item(), Some(describe(&error))),
            Err(_) => (track.empty_item(), None),
        };
        shared.update(|state| state.finish_encode(source, data_revision, sample, item, problem));
    }
}

/// Waits for a sample to encode and says which revision and snapshot it was claimed under; `None` once the viewer is
/// closed.
fn next_sample(shared: &Shared, source: usize) -> Option<(u64, u32, Arc<TrackSnapshot>)> {
    let mut state = shared.lock();
    loop {
        if state.closed {
            return None;
        }
        if let Some((sample, data_revision)) = state.next_encode(source)
            && let Load::Ready(_, held) = &state.sources[source].load
        {
            return Some((sample, data_revision, Arc::clone(held)));
        }
        state = shared
            .work
            .wait(state)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
    }
}
