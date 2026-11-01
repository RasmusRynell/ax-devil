//! The thread of a source: it loads the track, then encodes the samples the viewer's window needs.

use std::{sync::Arc, thread};

use foundation::{ErrorCode, Interrupt, UserError};
use playlist_resolvers::OverlayRef;
use scene::{Scene, SceneTime, TimeSlice, serial::columns::encode_sample_item};
use scene_store::Track;

use crate::{
    sources::TrackSource,
    state::{Shared, describe},
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

/// Loads one source's track, records how it went, and then encodes samples until the viewer closes. A cancelled
/// load records nothing.
pub(crate) fn run(shared: &Shared, tracks: &dyn TrackSource, source: usize, overlay: &OverlayRef) {
    let _close = CloseOnPanic(shared);
    let opened = match tracks.open(overlay, shared.frame_size, &shared.cancel) {
        Ok(track) => track,
        Err(Interrupt::Cancelled) => return,
        Err(Interrupt::Failed(error)) => {
            shared.update(|state| state.finish_load(source, Err(error), &shared.timeline));
            return;
        }
    };
    let mean = mean_item_bytes(shared, &opened);
    shared.update(|state| state.finish_load(source, Ok((opened.clone(), mean)), &shared.timeline));
    encode(shared, source, &opened);
}

/// The mean size of the items of up to `MEAN_SAMPLES` evenly spaced samples; samples that cannot be read do not
/// count.
fn mean_item_bytes(shared: &Shared, track: &Track) -> u32 {
    let count = track.keys().len();
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
/// sample that cannot be read or encoded still goes, as an empty item with a problem, so no lane waits for it.
fn encode(shared: &Shared, source: usize, track: &Track) {
    let mut empty: Option<Arc<[u8]>> = None;
    while let Some((sample, data_revision)) = next_sample(shared, source) {
        let (item, problem) = match usize::try_from(sample).map(|index| track.item(index)) {
            Ok(Ok(item)) => (item, None),
            Ok(Err(error)) => (empty_item(&mut empty, track), Some(describe(&error))),
            Err(_) => (empty_item(&mut empty, track), None),
        };
        shared.update(|state| state.finish_encode(source, data_revision, sample, item, problem));
    }
}

/// Waits for a sample to encode; `None` once the viewer is closed.
fn next_sample(shared: &Shared, source: usize) -> Option<(u64, u32)> {
    let mut state = shared.lock();
    loop {
        if state.closed {
            return None;
        }
        if let Some(next) = state.next_encode(source) {
            return Some(next);
        }
        state = shared
            .work
            .wait(state)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
    }
}

/// The item of a Scene with no entities, made once per source.
fn empty_item(held: &mut Option<Arc<[u8]>>, track: &Track) -> Arc<[u8]> {
    Arc::clone(held.get_or_insert_with(|| {
        let scene = Scene::new(TimeSlice::at(SceneTime::Frame(0)));
        let bytes = encode_sample_item(&scene, track.filter()).unwrap_or_else(|error| {
            tracing::error!("cannot encode an empty Sample Item: {error}");
            Vec::new()
        });
        bytes.into()
    }))
}
