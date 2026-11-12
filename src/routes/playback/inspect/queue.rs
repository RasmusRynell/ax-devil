//! One running inspection and one replaceable pending job, owned by their capability.
use super::{Outgoing, Request, Target, answer, unavailable};
use tokio::task::JoinHandle;

/// A request and the sample it may read, decided when the request arrived.
pub(super) struct Job {
    pub(super) request: Request,
    /// `None` when the viewer does not hold the sample.
    pub(super) target: Option<Target>,
}

/// The requests of one kind: one runs at a time, the newest waits for it.
#[derive(Default)]
pub(super) struct Queue {
    pub(super) running: Option<(Request, JoinHandle<Outgoing>)>,
    pub(super) queued: Option<Job>,
}

impl Queue {
    pub(super) fn cancel_pending(&mut self) {
        self.queued = None;
    }

    pub(super) fn ask(&mut self, request: Request, target: Option<Target>) {
        let target = target.filter(|_| request.ask.within_bounds());
        self.queued = Some(Job { request, target });
        self.start();
    }

    /// The next answer or token refusal; waits while there is neither.
    pub(super) async fn next(&mut self) -> Outgoing {
        let Some((request, running)) = self.running.as_mut() else {
            return std::future::pending().await;
        };
        // Only the handle is polled, so a dropped call loses nothing.
        let finished = running.await;
        let request = request.clone();
        self.running = None;
        self.start();
        finished.unwrap_or_else(|error| {
            tracing::warn!("An inspection failed: {error}");
            unavailable(&request)
        })
    }

    fn start(&mut self) {
        if self.running.is_none()
            && let Some(job) = self.queued.take()
        {
            if job.request.validity.is_cancelled() {
                return;
            }
            let request = job.request.clone();
            self.running = Some((request, tokio::task::spawn_blocking(move || answer(job))));
        }
    }
}

impl Drop for Queue {
    fn drop(&mut self) {
        if let Some((request, _)) = &self.running {
            request.validity.cancel();
        }
        self.cancel_pending();
    }
}
