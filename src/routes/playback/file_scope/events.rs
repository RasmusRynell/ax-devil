//! Answers to `EventQuery` and `Event`: the events a lane's whole file holds, placed on the video, and one of them
//! with its objects. Like `Appearances` each lane has one of each running, a newer request stops the older, and
//! neither waits for the lane's objects list or for the other; they share its kept history and placement, and the
//! event placement kept with them.

use super::{Cache, MAX_BINS, MAX_LIMIT, Reply, Stop, found, latest::Latest, read};
use api_types::{
    Audience,
    playback::{EventAskWire, EventQueryWire, FileAnswer, FileBody},
};
use foundation::CancelToken;
use overlays::{FileScopeInput, ViewerOverlays};
use scene_store::{EventQuery, EventView};
use std::sync::Arc;

/// A request about events.
pub(in crate::routes::playback) enum Ask {
    /// Rows of the list.
    List(EventQueryWire),
    /// One event.
    Detail(EventAskWire),
}

impl Ask {
    pub(super) fn lane(&self) -> u16 {
        match self {
            Self::List(wire) => wire.lane,
            Self::Detail(wire) => wire.lane,
        }
    }
}

/// A viewer's event requests.
pub(super) struct Events {
    audience: Audience,
    list: Latest,
    detail: Latest,
}

impl Events {
    pub(super) fn new(audience: Audience) -> Self {
        Self {
            audience,
            list: Latest::new(audience),
            detail: Latest::new(audience),
        }
    }

    /// Takes a request, replacing the lane's older one of its kind. A request that asks for too much only gets the
    /// owner told.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        cache: &Arc<Cache>,
        own: bool,
        ask: Ask,
    ) {
        match ask {
            Ask::List(wire) => {
                let (lane, request) = (wire.lane, wire.request);
                if wire.limit == 0 || wire.limit > MAX_LIMIT {
                    self.list.release(lane);
                    if self.audience.host_details {
                        let reason = format!(
                            "An event query asks for 1 to {MAX_LIMIT} rows, not {}",
                            wire.limit
                        );
                        self.list.say(Reply::problem(lane, request, reason));
                    }
                    return;
                }
                let answer =
                    move |input: &FileScopeInput, cache: &Cache, cancel: &CancelToken, audience| {
                        list(&wire, input, cache, cancel, audience)
                    };
                self.list.ask(overlays, cache, own, (lane, request), answer);
            }
            Ask::Detail(wire) => {
                let (lane, request) = (wire.lane, wire.request);
                if wire.bins > MAX_BINS {
                    self.detail.release(lane);
                    if self.audience.host_details {
                        let reason = format!(
                            "An event request asks for 0 to {MAX_BINS} spans, not {}",
                            wire.bins
                        );
                        self.detail.say(Reply::problem(lane, request, reason));
                    }
                    return;
                }
                let answer =
                    move |input: &FileScopeInput, cache: &Cache, cancel: &CancelToken, audience| {
                        detail(&wire, input, cache, cancel, audience)
                    };
                self.detail
                    .ask(overlays, cache, own, (lane, request), answer);
            }
        }
    }

    /// The lane's events are no longer wanted: its running requests stop without an answer.
    pub(super) fn release(&mut self, lane: u16) {
        self.list.release(lane);
        self.detail.release(lane);
    }

    /// The next answer; never ends, and waits while there is none.
    pub(super) async fn next(&mut self) -> Reply {
        tokio::select! {
            reply = self.list.next() => reply,
            reply = self.detail.next() => reply,
        }
    }
}

/// The rows asked for, from the lane's kept history and placements when they are of the revisions `input` has.
fn list(
    ask: &EventQueryWire,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<FileAnswer, Stop> {
    let lane = ask.lane;
    let cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let revisions = (cached.data_revision, cached.policy_revision);
    let matches = match &cached.event_listing {
        Some((search, matches)) if *search == ask.query => Arc::clone(matches),
        _ => {
            // The old listing goes while the new one is made.
            cache.update(lane, revisions, |kept| kept.event_listing = None);
            let query = EventQuery {
                search: ask.query.clone(),
            };
            Arc::new(query.run(&cached.history, &cached.events, cancel)?)
        }
    };
    let view = EventView {
        history: &cached.history,
        events: &cached.events,
        placement: &cached.placement,
    };
    let answer = found(
        &cached,
        FileBody::Events {
            total: matches.total(),
            matched: matches.matched(),
            offset: ask.offset,
            rows: matches.rows(view, ask.offset as usize, ask.limit as usize),
            located: ask.locate.and_then(|event| matches.row_of(event)),
            frame_row: ask
                .frame
                .map(|frame| matches.frame_row(&cached.events, frame)),
        },
    );
    let search = ask.query.clone();
    cache.update(lane, revisions, |kept| {
        kept.event_listing = Some((search, matches));
    });
    Ok(answer)
}

/// The event asked for.
fn detail(
    ask: &EventAskWire,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<FileAnswer, Stop> {
    let lane = ask.lane;
    let cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let view = EventView {
        history: &cached.history,
        events: &cached.events,
        placement: &cached.placement,
    };
    let body = FileBody::Event {
        event: ask.event,
        detail: view.detail(ask.event, ask.bins, MAX_LIMIT as usize),
    };
    Ok(found(&cached, body))
}
