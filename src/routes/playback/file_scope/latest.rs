//! Requests of which a lane has one at a time: a newer request stops the older, which is not answered unless it had
//! ended, and none waits for another kind of request. `Appearances` and the event requests run this way beside the
//! lane's objects list.

use super::{Cache, Reply, Stop, input};
use api_types::{Audience, playback::FileAnswer};
use foundation::CancelToken;
use overlays::{FileScopeInput, ViewerOverlays};
use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
};
use tokio::task::{Id, JoinSet};

type Job = Box<dyn FnOnce(CancelToken) -> Option<Reply> + Send>;

/// One running and one replaceable pending job per lane.
pub(super) struct Latest {
    audience: Audience,
    /// A running task stays registered until it leaves the blocking pool, even after cancellation.
    running: HashMap<u16, CancelToken>,
    tasks: JoinSet<Option<Reply>>,
    lanes: HashMap<Id, (u16, u32)>,
    waiting: HashMap<u16, (u32, Job)>,
    /// Answers already known, in the order asked.
    ready: VecDeque<Reply>,
}

impl Latest {
    pub(super) fn new(audience: Audience) -> Self {
        Self {
            audience,
            running: HashMap::new(),
            tasks: JoinSet::new(),
            lanes: HashMap::new(),
            waiting: HashMap::new(),
            ready: VecDeque::new(),
        }
    }

    /// Takes request `request` of `lane`, replacing the lane's older one. `own` says whether the viewer chooses its
    /// own frames; if not, or if the lane has no file to ask, the answer is that it is unavailable at once. Otherwise
    /// `answer` runs on a blocking thread, after the lane's cancelled predecessor has ended.
    pub(super) fn ask(
        &mut self,
        overlays: &ViewerOverlays,
        cache: &Arc<Cache>,
        own: bool,
        (lane, request): (u16, u32),
        answer: impl FnOnce(&FileScopeInput, &Cache, &CancelToken, Audience) -> Result<FileAnswer, Stop>
        + Send
        + 'static,
    ) {
        self.release(lane);
        match input(overlays, own, lane, self.audience) {
            Ok(input) => {
                cache.open(lane);
                let (cache, audience) = (Arc::clone(cache), self.audience);
                let job: Job = Box::new(move |token| {
                    token.check().ok()?;
                    match answer(&input, &cache, &token, audience) {
                        Ok(answer) => Some(Reply::file(lane, request, answer)),
                        Err(Stop::Cancelled) => None,
                        Err(Stop::Unavailable(why)) => Some(Reply::unavailable(lane, request, why)),
                    }
                });
                if self.running.contains_key(&lane) {
                    self.waiting.insert(lane, (request, job));
                } else {
                    self.start(lane, request, job);
                }
            }
            Err(why) => self.say(Reply::unavailable(lane, request, why)),
        }
    }

    fn start(&mut self, lane: u16, request: u32, job: Job) {
        let cancel = CancelToken::new();
        let token = cancel.clone();
        let task = self.tasks.spawn_blocking(move || job(token));
        self.lanes.insert(task.id(), (lane, request));
        self.running.insert(lane, cancel);
    }

    /// Queues `reply` as the answer to its request.
    pub(super) fn say(&mut self, reply: Reply) {
        self.ready.push_back(reply);
    }

    /// The lane's request is no longer wanted: it stops without an answer.
    pub(super) fn release(&mut self, lane: u16) {
        if let Some(cancel) = self.running.get(&lane) {
            cancel.cancel();
        }
        self.waiting.remove(&lane);
        self.ready.retain(|reply| reply.lane != lane);
    }

    /// The next answer; never ends, and waits while there is none.
    pub(super) async fn next(&mut self) -> Reply {
        loop {
            if let Some(reply) = self.ready.pop_front() {
                return reply;
            }
            // Only the set is polled, so a dropped call loses nothing.
            let Some(joined) = self.tasks.join_next_with_id().await else {
                return std::future::pending().await;
            };
            let id = match &joined {
                Ok((id, _)) => *id,
                Err(error) => error.id(),
            };
            let Some((lane, request)) = self.lanes.remove(&id) else {
                continue;
            };
            let current = self
                .running
                .remove(&lane)
                .is_some_and(|token| !token.is_cancelled());
            if let Some((waiting, job)) = self.waiting.remove(&lane) {
                self.start(lane, waiting, job);
            }
            match joined {
                Ok((_, Some(reply))) if current => return reply,
                Err(error) => {
                    tracing::warn!("A file scope request failed: {error}");
                    // A crashed query that was replaced is not wanted; the current one would otherwise never be answered.
                    if current {
                        return Reply::crashed(lane, request, self.audience);
                    }
                }
                _ => {}
            }
        }
    }
}

impl Drop for Latest {
    fn drop(&mut self) {
        for cancel in self.running.values() {
            cancel.cancel();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::{Mutex, mpsc},
        time::Duration,
    };

    #[tokio::test]
    async fn held_preparation_bounds_the_burst_and_release_discards_detail_only() {
        tokio::time::timeout(Duration::from_secs(10), async {
            let dir = tempfile::tempdir().unwrap();
            let (viewer, _) = super::super::tests::viewer(dir.path()).await;
            let cache = Arc::new(Cache::new(1 << 20));
            let mut detail = Latest::new(Audience::HOST);
            let ran = Arc::new(Mutex::new(Vec::new()));
            let (entered, started) = tokio::sync::oneshot::channel();
            let (resume, released) = mpsc::channel();
            let observed = ran.clone();
            detail.ask(
                &viewer,
                &cache,
                true,
                (0, 1),
                move |input, cache, cancel, audience| {
                    observed.lock().unwrap().push(1);
                    cache.prepared(0, input, || {
                        entered.send(()).unwrap();
                        released.recv_timeout(Duration::from_secs(5)).unwrap();
                        super::super::read(input, cancel, audience)
                    })?;
                    cancel.check()?;
                    Ok(FileAnswer::Unavailable {
                        unavailable: api_types::playback::FileScopeUnavailable::Reading,
                    })
                },
            );
            started.await.unwrap();
            for request in 2..=100 {
                let observed = ran.clone();
                detail.ask(
                    &viewer,
                    &cache,
                    true,
                    (0, request),
                    move |_, _, cancel, _| {
                        cancel.check()?;
                        observed.lock().unwrap().push(request);
                        Ok(FileAnswer::Unavailable {
                            unavailable: api_types::playback::FileScopeUnavailable::Reading,
                        })
                    },
                );
            }
            assert_eq!(detail.tasks.len(), 1);
            assert_eq!(detail.waiting.len(), 1);
            assert_eq!(*ran.lock().unwrap(), [1]);
            resume.send(()).unwrap();
            assert!(matches!(
                detail.next().await.message,
                api_types::playback::ServerMessage::File { request: 100, .. }
            ));
            assert_eq!(*ran.lock().unwrap(), [1, 100]);
            // The detail's independent release drops pending work even when another subscription holds the cache.
            detail.ask(&viewer, &cache, true, (0, 101), |_, _, _, _| {
                Ok(FileAnswer::Unavailable {
                    unavailable: api_types::playback::FileScopeUnavailable::Reading,
                })
            });
            detail.release(0);
            assert!(
                tokio::time::timeout(Duration::from_millis(50), detail.next())
                    .await
                    .is_err()
            );
        })
        .await
        .expect("whole test deadline");
    }

    #[tokio::test]
    async fn a_query_that_panics_is_answered_as_failed_and_the_next_one_still_runs() {
        tokio::time::timeout(Duration::from_secs(10), async {
            let dir = tempfile::tempdir().unwrap();
            let (viewer, _) = super::super::tests::viewer(dir.path()).await;
            let cache = Arc::new(Cache::new(1 << 20));
            let mut latest = Latest::new(Audience::HOST);
            latest.ask(&viewer, &cache, true, (0, 1), |_, _, _, _| {
                panic!("the query has a defect")
            });
            assert!(matches!(
                latest.next().await.message,
                api_types::playback::ServerMessage::File {
                    request: 1,
                    answer: FileAnswer::Unavailable {
                        unavailable: api_types::playback::FileScopeUnavailable::Failed(_)
                    },
                    ..
                }
            ));
            latest.ask(&viewer, &cache, true, (0, 2), |_, _, _, _| {
                Ok(FileAnswer::Unavailable {
                    unavailable: api_types::playback::FileScopeUnavailable::Reading,
                })
            });
            assert!(matches!(
                latest.next().await.message,
                api_types::playback::ServerMessage::File {
                    request: 2,
                    answer: FileAnswer::Unavailable {
                        unavailable: api_types::playback::FileScopeUnavailable::Reading
                    },
                    ..
                }
            ));
        })
        .await
        .expect("whole test deadline");
    }
}
