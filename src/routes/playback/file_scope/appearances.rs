//! Answers to `Appearances`: the exact frames one object is on, and the events that involve it. They share the lane's
//! kept history and placement with the list but not its job: a request for spans never cancels or waits for the
//! lane's list query (only for the list to finish reading the history and placement they share, which is then read
//! once), and its spans keep every gap, however short, so a jump never skips an absence.

use super::{Cache, Stop, found, read};
use api_types::{
    Audience,
    playback::{FileAnswer, FileBody},
};
use foundation::CancelToken;
use overlays::FileScopeInput;
use scene_store::EventView;

/// The most spans, and the most events, one answer carries; an object with more has its first ones sent.
const MAX_SPANS: usize = 8192;

/// What an `Appearances` request asked for.
pub(in crate::routes::playback) struct Ask {
    pub(in crate::routes::playback) lane: u16,
    pub(in crate::routes::playback) request: u32,
    pub(in crate::routes::playback) entity: String,
}

/// The spans of the object, from the lane's kept history and placement when they are of the revisions `input` has.
pub(super) fn answer(
    ask: &Ask,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<FileAnswer, Stop> {
    let lane = ask.lane;
    let cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let mut spans = scene_store::appearances(&cached.history, &cached.placement, &ask.entity);
    let truncated = spans.len() > MAX_SPANS;
    spans.truncate(MAX_SPANS);
    let (events, events_truncated) = EventView {
        history: &cached.history,
        events: &cached.events,
        placement: &cached.placement,
    }
    .object_events(&ask.entity, MAX_SPANS);
    let body = FileBody::Appearances {
        entity: ask.entity.clone(),
        spans,
        truncated,
        events,
        events_truncated,
    };
    Ok(found(&cached, body))
}
