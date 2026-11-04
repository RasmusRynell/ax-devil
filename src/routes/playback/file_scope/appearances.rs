//! Answers to `Appearances`: the exact frames one object is on. They share the lane's kept history and placement with
//! the list but not its job: a request for spans never cancels or waits for the lane's list query (only for the list
//! to finish reading the history and placement they share, which is then read once), and its spans keep
//! every gap, however short, so a jump never skips an absence.

use super::{Cache, Reply, Stop, read};
use api_types::{
    Audience,
    playback::{FileAppearances, FileScopeUnavailable, ServerMessage},
    project,
};
use foundation::CancelToken;
use overlays::{FileScopeBlocked, FileScopeInput, ViewerOverlays};
use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
};
use tokio::task::JoinSet;

/// The most spans one answer carries; an object with more has its first ones sent.
const MAX_SPANS: usize = 8192;

/// What an `Appearances` request asked for.
pub(in crate::routes::playback) struct Ask {
    pub(in crate::routes::playback) lane: u16,
    pub(in crate::routes::playback) request: u32,
    pub(in crate::routes::playback) entity: String,
}

/// A viewer's `Appearances` requests.
pub(super) struct Spans {
    audience: Audience,
    /// The newest request of each lane and the token that stops it.
    running: HashMap<u16, (u32, CancelToken)>,
    tasks: JoinSet<Option<Reply>>,
    /// Answers already known, in the order asked.
    ready: VecDeque<Reply>,
}

impl Spans {
    pub(super) fn new(audience: Audience) -> Self {
        Self {
            audience,
            running: HashMap::new(),
            tasks: JoinSet::new(),
            ready: VecDeque::new(),
        }
    }

    /// Takes a request, replacing the lane's older one: that one stops and is not answered unless it had ended.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        cache: &Arc<Cache>,
        own: bool,
        ask: Ask,
    ) {
        let (lane, ask_request) = (ask.lane, ask.request);
        self.release(lane);
        let unavailable = |unavailable| Reply {
            lane,
            request: ask.request,
            message: ServerMessage::Appearances {
                lane,
                request: ask.request,
                entity: ask.entity.clone(),
                appearances: FileAppearances::Unavailable { unavailable },
            },
        };
        if !own {
            self.ready
                .push_back(unavailable(FileScopeUnavailable::Following));
            return;
        }
        match overlays.file_scope(lane) {
            Ok(input) => {
                cache.open(lane);
                let (cache, audience) = (Arc::clone(cache), self.audience);
                let cancel = CancelToken::new();
                let token = cancel.clone();
                self.tasks.spawn_blocking(move || {
                    match answer(&ask, &input, &cache, &token, audience) {
                        Ok(message) => Some(Reply {
                            lane,
                            request: ask.request,
                            message,
                        }),
                        Err(Stop::Cancelled) => None,
                        Err(Stop::Unavailable(why)) => Some(Reply {
                            lane,
                            request: ask.request,
                            message: ServerMessage::Appearances {
                                lane,
                                request: ask.request,
                                entity: ask.entity,
                                appearances: FileAppearances::Unavailable { unavailable: why },
                            },
                        }),
                    }
                });
                self.running.insert(lane, (ask_request, cancel));
            }
            Err(blocked) => {
                let why = match blocked {
                    FileScopeBlocked::Plain => FileScopeUnavailable::PlainLane,
                    FileScopeBlocked::Reading => FileScopeUnavailable::Reading,
                    FileScopeBlocked::Failed(error) => {
                        FileScopeUnavailable::Failed(project(&error, self.audience))
                    }
                };
                self.ready.push_back(unavailable(why));
            }
        }
    }

    /// The lane's spans are no longer wanted: its running request stops without an answer.
    pub(super) fn release(&mut self, lane: u16) {
        if let Some((_, cancel)) = self.running.remove(&lane) {
            cancel.cancel();
        }
        self.ready.retain(|reply| reply.lane != lane);
    }

    /// The next answer; never ends, and waits while there is none.
    pub(super) async fn next(&mut self) -> Reply {
        loop {
            if let Some(reply) = self.ready.pop_front() {
                return reply;
            }
            // Only the set is polled, so a dropped call loses nothing.
            match self.tasks.join_next().await {
                None => return std::future::pending().await,
                // Only the lane's newest request is answered: what a released lane or a replaced request finished
                // meanwhile is not.
                Some(Ok(Some(reply)))
                    if self
                        .running
                        .get(&reply.lane)
                        .is_some_and(|(request, _)| *request == reply.request) =>
                {
                    return reply;
                }
                Some(Ok(_)) => {}
                Some(Err(error)) => tracing::warn!("An appearances request failed: {error}"),
            }
        }
    }
}

impl Drop for Spans {
    fn drop(&mut self) {
        for (_, cancel) in self.running.values() {
            cancel.cancel();
        }
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
    Ok(ServerMessage::Appearances {
        lane,
        request: ask.request,
        entity: ask.entity.clone(),
        appearances: FileAppearances::Found {
            data_revision: cached.data_revision,
            policy_revision: cached.policy_revision,
            spans,
            truncated,
        },
    })
}
