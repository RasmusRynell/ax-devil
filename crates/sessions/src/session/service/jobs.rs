//! Job completion under the Session lock, with cumulative reload validation.

use super::{Deferred, Input, Service, SessionId, apply};
use crate::session::{
    Completer, Completion, JobId, JobOwner, Retry, Watcher, transition::uncovered,
};
use workspace::store::Reload;

// Retry transfers the single resolved Content by value between rounds, without an extra heap owner.
#[allow(clippy::result_large_err)]
impl Service {
    pub(super) fn run(&self, deferred: Deferred) {
        for (session, spawn) in deferred.spawn {
            let done = self.completer(session, spawn.job, Watcher::default());
            self.inner.runner.run(spawn, done);
        }
    }

    fn completer(&self, session: SessionId, job: JobId, watcher: Watcher) -> Completer {
        let service = self.clone();
        Completer {
            complete: Box::new(move |completion, watcher| {
                service.finish(session, job, completion, watcher)
            }),
            watcher,
        }
    }

    /// Completes a job, or returns the retained Content and entries still to validate. Stale jobs are dropped.
    pub fn complete(
        &self,
        session: SessionId,
        job: JobId,
        completion: Completion,
    ) -> Result<(), Retry> {
        self.finish(session, job, completion, Watcher::default())
    }

    fn finish(
        &self,
        session: SessionId,
        job: JobId,
        completion: Completion,
        watcher: Watcher,
    ) -> Result<(), Retry> {
        self.locked(|sessions, deferred| {
            let missing = sessions
                .get(&session)
                .map(|live| uncovered(&live.state, job, &completion))
                .unwrap_or_default();
            if !missing.is_empty() {
                // `uncovered` returns entries only for a successful resolution.
                let Completion::Resolved {
                    result: Ok(content),
                    validated,
                } = completion
                else {
                    unreachable!("only successful reloads need another validation round");
                };
                return Err(Retry {
                    done: self.completer(session, job, watcher),
                    content,
                    validated,
                    missing,
                });
            }
            let failed_reload = sessions.get(&session).and_then(|live| {
                if !matches!(&completion, Completion::Resolved { result: Err(_), .. }) {
                    return None;
                }
                let JobOwner::Doc { item, attempt } = live.state.jobs.get(&job)?.owner;
                matches!(live.state.doc.workspace.reloads.get(&item), Some(Reload::Running { attempt: running, .. }) if *running == attempt).then_some(item)
            });
            let wanted = sessions
                .get(&session)
                .is_some_and(|live| live.state.jobs.contains_key(&job));
            let applied = apply(
                sessions,
                session,
                Input::Complete { job, completion },
                deferred,
            );
            let read = watcher.take();
            if wanted && applied.is_ok() {
                if let Some(item) = failed_reload {
                    self.inner.sources.record_failed(session, item, read.map(|(_, watch)| watch));
                } else if let Some((item, watch)) = read {
                    self.inner.sources.record(session, item, watch);
                }
            }
            Ok(())
        })
    }
}
