//! Requests of which a lane has one at a time: a newer request stops the older, which is not answered unless it had
//! ended, and none waits for another kind of request. `Appearances` and the event requests run this way beside the
//! lane's objects list.

use super::{Cache, Reply, Stop};
use api_types::{
    Audience,
    playback::{FileScopeUnavailable, ServerMessage},
    project,
};
use foundation::CancelToken;
use overlays::{FileScopeBlocked, FileScopeInput, ViewerOverlays};
use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
};
use tokio::task::JoinSet;

/// A kind of request, of which each lane has its newest running.
pub(super) struct Latest {
    audience: Audience,
    /// The newest request of each lane and the token that stops it.
    running: HashMap<u16, (u32, CancelToken)>,
    tasks: JoinSet<Option<Reply>>,
    /// Answers already known, in the order asked.
    ready: VecDeque<Reply>,
}

impl Latest {
    pub(super) fn new(audience: Audience) -> Self {
        Self {
            audience,
            running: HashMap::new(),
            tasks: JoinSet::new(),
            ready: VecDeque::new(),
        }
    }

    /// Takes request `request` of `lane`, replacing the lane's older one. `own` says whether the viewer chooses its
    /// own frames; if not, or if the lane has no file to ask, the answer is `unavailable` at once. Otherwise `answer`
    /// runs on a blocking thread.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        cache: &Arc<Cache>,
        own: bool,
        (lane, request): (u16, u32),
        unavailable: impl Fn(FileScopeUnavailable) -> ServerMessage + Send + 'static,
        answer: impl FnOnce(
            &FileScopeInput,
            &Cache,
            &CancelToken,
            Audience,
        ) -> Result<ServerMessage, Stop>
        + Send
        + 'static,
    ) {
        self.release(lane);
        if !own {
            self.say(lane, request, unavailable(FileScopeUnavailable::Following));
            return;
        }
        match overlays.file_scope(lane) {
            Ok(input) => {
                cache.open(lane);
                let (cache, audience) = (Arc::clone(cache), self.audience);
                let cancel = CancelToken::new();
                let token = cancel.clone();
                self.tasks
                    .spawn_blocking(move || match answer(&input, &cache, &token, audience) {
                        Ok(message) => Some(Reply {
                            lane,
                            request,
                            message,
                        }),
                        Err(Stop::Cancelled) => None,
                        Err(Stop::Unavailable(why)) => Some(Reply {
                            lane,
                            request,
                            message: unavailable(why),
                        }),
                    });
                self.running.insert(lane, (request, cancel));
            }
            Err(blocked) => {
                let why = match blocked {
                    FileScopeBlocked::Plain => FileScopeUnavailable::PlainLane,
                    FileScopeBlocked::Reading => FileScopeUnavailable::Reading,
                    FileScopeBlocked::Failed(error) => {
                        FileScopeUnavailable::Failed(project(&error, self.audience))
                    }
                };
                self.say(lane, request, unavailable(why));
            }
        }
    }

    /// Queues `message` as the answer to request `request` of `lane`.
    pub(super) fn say(&mut self, lane: u16, request: u32, message: ServerMessage) {
        self.ready.push_back(Reply {
            lane,
            request,
            message,
        });
    }

    /// The lane's request is no longer wanted: it stops without an answer.
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
                Some(Err(error)) => tracing::warn!("A file scope request failed: {error}"),
            }
        }
    }
}

impl Drop for Latest {
    fn drop(&mut self) {
        for (_, cancel) in self.running.values() {
            cancel.cancel();
        }
    }
}
