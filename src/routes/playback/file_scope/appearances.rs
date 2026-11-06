//! Answers to `Appearances`: the exact frames one object is on, and the events that involve it. They share the lane's
//! kept history and placement with the list but not its job: a request for spans never cancels or waits for the
//! lane's list query (only for the list to finish reading the history and placement they share, which is then read
//! once), and its spans keep every gap, however short, so a jump never skips an absence.

use super::{Cache, Reply, Stop, latest::Latest, read};
use api_types::{
    Audience,
    playback::{FileAppearances, ServerMessage},
};
use foundation::CancelToken;
use overlays::{FileScopeInput, ViewerOverlays};
use scene_store::EventView;
use std::sync::Arc;

/// The most spans, and the most events, one answer carries; an object with more has its first ones sent.
const MAX_SPANS: usize = 8192;

/// What an `Appearances` request asked for.
pub(in crate::routes::playback) struct Ask {
    pub(in crate::routes::playback) lane: u16,
    pub(in crate::routes::playback) request: u32,
    pub(in crate::routes::playback) entity: String,
}

/// A viewer's `Appearances` requests.
pub(super) struct Spans(Latest);

impl Spans {
    pub(super) fn new(audience: Audience) -> Self {
        Self(Latest::new(audience))
    }

    /// Takes a request, replacing the lane's older one: that one stops and is not answered unless it had ended.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        cache: &Arc<Cache>,
        own: bool,
        ask: Ask,
    ) {
        let (lane, request, entity) = (ask.lane, ask.request, ask.entity.clone());
        let unavailable = move |unavailable| ServerMessage::Appearances {
            lane,
            request,
            entity: entity.clone(),
            appearances: FileAppearances::Unavailable { unavailable },
        };
        let answer =
            move |input: &FileScopeInput, cache: &Cache, cancel: &CancelToken, audience| {
                answer(&ask, input, cache, cancel, audience)
            };
        self.0
            .ask(overlays, cache, own, (lane, request), unavailable, answer);
    }

    /// The lane's spans are no longer wanted: its running request stops without an answer.
    pub(super) fn release(&mut self, lane: u16) {
        self.0.release(lane);
    }

    /// The next answer; never ends, and waits while there is none.
    pub(super) async fn next(&mut self) -> Reply {
        self.0.next().await
    }
}

/// The spans of the object, from the lane's kept history and placement when they are of the revisions `input` has.
fn answer(
    ask: &Ask,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<ServerMessage, Stop> {
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
    Ok(ServerMessage::Appearances {
        lane,
        request: ask.request,
        entity: ask.entity.clone(),
        appearances: FileAppearances::Found {
            data_revision: cached.data_revision,
            policy_revision: cached.policy_revision,
            spans,
            truncated,
            events,
            events_truncated,
        },
    })
}
