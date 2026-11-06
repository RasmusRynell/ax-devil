//! Answers to `EventQuery` and `Event`: the events a lane's whole file holds, placed on the video, and one of them
//! with its objects. Like `Appearances` each lane has one of each running, a newer request stops the older, and
//! neither waits for the lane's objects list or for the other; they share its kept history and placement, and the
//! event placement kept with them.

use super::{Cache, MAX_BINS, MAX_LIMIT, Reply, Stop, latest::Latest, read};
use api_types::{
    Audience,
    playback::{
        EventAskWire, EventQueryWire, EventRowsWire, EventWire, FileEvent, FileEvents,
        ServerMessage,
    },
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
                        let problem = ServerMessage::LaneProblem { lane, reason };
                        self.list.say(lane, request, problem);
                    }
                    return;
                }
                let unavailable = move |unavailable| {
                    ServerMessage::EventRows(EventRowsWire {
                        lane,
                        request,
                        rows: FileEvents::Unavailable { unavailable },
                    })
                };
                let answer =
                    move |input: &FileScopeInput, cache: &Cache, cancel: &CancelToken, audience| {
                        list(&wire, input, cache, cancel, audience)
                    };
                self.list
                    .ask(overlays, cache, own, (lane, request), unavailable, answer);
            }
            Ask::Detail(wire) => {
                let (lane, request, event) = (wire.lane, wire.request, wire.event);
                if wire.bins > MAX_BINS {
                    self.detail.release(lane);
                    if self.audience.host_details {
                        let reason = format!(
                            "An event request asks for 0 to {MAX_BINS} spans, not {}",
                            wire.bins
                        );
                        let problem = ServerMessage::LaneProblem { lane, reason };
                        self.detail.say(lane, request, problem);
                    }
                    return;
                }
                let unavailable = move |unavailable| {
                    ServerMessage::Event(EventWire {
                        lane,
                        request,
                        event,
                        detail: FileEvent::Unavailable { unavailable },
                    })
                };
                let answer =
                    move |input: &FileScopeInput, cache: &Cache, cancel: &CancelToken, audience| {
                        detail(&wire, input, cache, cancel, audience)
                    };
                self.detail
                    .ask(overlays, cache, own, (lane, request), unavailable, answer);
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
) -> Result<ServerMessage, Stop> {
    let lane = ask.lane;
    let mut cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let matches = match &cached.event_listing {
        Some((search, matches)) if *search == ask.query => Arc::clone(matches),
        _ => {
            // What was computed stays for the next request even when this one is cancelled.
            cached.event_listing = None;
            cache.put(lane, cached.clone());
            let query = EventQuery {
                search: ask.query.clone(),
            };
            let matches = Arc::new(query.run(&cached.history, &cached.events, cancel)?);
            cached.event_listing = Some((ask.query.clone(), Arc::clone(&matches)));
            matches
        }
    };
    let view = EventView {
        history: &cached.history,
        events: &cached.events,
        placement: &cached.placement,
    };
    let message = ServerMessage::EventRows(EventRowsWire {
        lane,
        request: ask.request,
        rows: FileEvents::Found {
            data_revision: cached.data_revision,
            policy_revision: cached.policy_revision,
            total: matches.total(),
            matched: matches.matched(),
            offset: ask.offset,
            rows: matches.rows(view, ask.offset as usize, ask.limit as usize),
            located: ask.locate.and_then(|event| matches.row_of(event)),
            frame_row: ask
                .frame
                .map(|frame| matches.frame_row(&cached.events, frame)),
        },
    });
    cache.put(lane, cached);
    Ok(message)
}

/// The event asked for.
fn detail(
    ask: &EventAskWire,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<ServerMessage, Stop> {
    let lane = ask.lane;
    let cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let view = EventView {
        history: &cached.history,
        events: &cached.events,
        placement: &cached.placement,
    };
    Ok(ServerMessage::Event(EventWire {
        lane,
        request: ask.request,
        event: ask.event,
        detail: FileEvent::Found {
            data_revision: cached.data_revision,
            policy_revision: cached.policy_revision,
            detail: view.detail(ask.event, ask.bins),
        },
    }))
}
