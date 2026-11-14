//! Readying the next playlist entry in the background: its videos' frame indexes and its overlay files' artifacts are
//! built into the cache folder while the viewer plays the entry before it, so stepping on opens from the cache.
//!
//! One worker thread serves the whole backend, started on the first request, and it holds one pending request: a new
//! request replaces the pending one (the latest wins) and cancels the job that runs, whichever viewer asked. Dropping
//! a [`Preparation`] withdraws its request or cancels its job, and the backend stopping ends the worker. A job reads one
//! file after the other; cancelling stops it where FFmpeg polls (a build's reads, an overlay's decode), but a read
//! stuck in a system call, such as a hung network mount, holds the worker until it returns, and requests wait in the
//! one slot meanwhile instead of starting more threads. The worker is one thread, but indexing a video that has no
//! packet index falls back to decoding it, where FFmpeg picks its own number of decoder threads.

use crate::video_sources::VideoSources;
use foundation::CancelToken;
use handler_api::OverlayRef;
use overlays::OverlayService;
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use tokio::sync::watch;
use workspace::{Entry, VideoRef};

/// What a preparation builds: the files of an entry that a viewer on it would open.
#[derive(Clone, Debug, PartialEq)]
pub struct Work {
    video: VideoRef,
    overlays: Vec<OverlayRef>,
}

impl Work {
    /// The video of `entry` and those of its overlays whose lane label is in `lanes`.
    pub fn of(entry: &Entry, lanes: &[String]) -> Self {
        Self {
            video: entry.video.clone(),
            overlays: entry
                .overlays
                .iter()
                .filter(|overlay| lanes.contains(&overlay.label))
                .cloned()
                .collect(),
        }
    }
}

/// A request, with the token that cancels it whether it waits or runs.
struct Job {
    work: Work,
    cancel: CancelToken,
}

#[derive(Default)]
struct State {
    /// The request that waits for the worker; a newer one replaces it.
    pending: Option<Job>,
    /// The token of the job that runs.
    running: Option<CancelToken>,
    started: bool,
    stopped: bool,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Ends the worker and cancels what waits and what runs.
    fn stop(&self) {
        let mut state = self.lock();
        state.stopped = true;
        state.pending = None;
        if let Some(running) = &state.running {
            running.cancel();
        }
        self.wake.notify_all();
    }
}

/// A running preparation; dropping it withdraws the request or cancels the job.
pub struct Preparation {
    cancel: CancelToken,
}

impl Drop for Preparation {
    fn drop(&mut self) {
        // A waiting request is skipped once cancelled, and a running job stops at its next poll.
        self.cancel.cancel();
    }
}

type Runner = Arc<dyn Fn(&Work, &CancelToken) + Send + Sync>;

/// Starts preparations, one at a time.
pub struct Preparer {
    shared: Arc<Shared>,
    run: Runner,
}

impl Drop for Preparer {
    fn drop(&mut self) {
        self.shared.stop();
    }
}

impl Preparer {
    /// A preparer that builds the caches of `videos` and `overlays` until `stopping` turns true. Must be created
    /// inside a Tokio runtime, which hears `stopping`.
    pub fn new(
        videos: Arc<VideoSources>,
        overlays: Arc<OverlayService>,
        mut stopping: watch::Receiver<bool>,
    ) -> Self {
        let run: Runner = Arc::new(move |work, cancel| {
            if let Err(error) = videos.warm_index(&work.video, cancel) {
                tracing::debug!("Not prepared: {error}");
            }
            for overlay in &work.overlays {
                if cancel.is_cancelled() {
                    return;
                }
                if let Err(error) = overlays.warm(overlay, cancel) {
                    tracing::debug!("Not prepared: {error}");
                }
            }
        });
        let preparer = Self::with_runner(run);
        let shared = Arc::clone(&preparer.shared);
        tokio::spawn(async move {
            let _ = stopping.wait_for(|stop| *stop).await;
            shared.stop();
        });
        preparer
    }

    fn with_runner(run: Runner) -> Self {
        Self {
            shared: Arc::new(Shared {
                state: Mutex::new(State::default()),
                wake: Condvar::new(),
            }),
            run,
        }
    }

    /// Asks for `work` to be built in the background, replacing the request that waits and cancelling the job that
    /// runs. Nothing is waited for and nothing fails: a file that cannot be read is logged and left for the viewer
    /// that opens it to report.
    pub fn start(&self, work: Work) -> Preparation {
        let cancel = CancelToken::new();
        let mut state = self.shared.lock();
        if state.stopped {
            cancel.cancel();
            return Preparation { cancel };
        }
        if let Some(replaced) = state.pending.replace(Job {
            work,
            cancel: cancel.clone(),
        }) {
            replaced.cancel.cancel();
        }
        if let Some(running) = &state.running {
            running.cancel();
        }
        if !state.started {
            let (shared, run) = (Arc::clone(&self.shared), Arc::clone(&self.run));
            match std::thread::Builder::new()
                .name("prepare-next".into())
                .spawn(move || work_through(&shared, &run))
            {
                Ok(_) => state.started = true,
                Err(error) => {
                    tracing::debug!("Cannot prepare the next entry: {error}");
                    state.pending = None;
                    cancel.cancel();
                }
            }
        }
        self.shared.wake.notify_one();
        Preparation { cancel }
    }
}

/// The worker: takes the pending request, runs it, and waits for the next until the preparer stops.
fn work_through(shared: &Shared, run: &Runner) {
    loop {
        let job = {
            let mut state = shared.lock();
            loop {
                if state.stopped {
                    return;
                }
                match state.pending.take() {
                    // Withdrawn while it waited.
                    Some(job) if job.cancel.is_cancelled() => {}
                    Some(job) => {
                        state.running = Some(job.cancel.clone());
                        break job;
                    }
                    None => {
                        state = shared
                            .wake
                            .wait(state)
                            .unwrap_or_else(PoisonError::into_inner);
                    }
                }
            }
        };
        run(&job.work, &job.cancel);
        shared.lock().running = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::mpsc::{self, RecvTimeoutError},
        thread::{self, ThreadId},
        time::{Duration, Instant},
    };

    /// Longest any step of these tests waits, so a regression fails instead of hanging.
    const DEADLINE: Duration = Duration::from_secs(10);

    fn work(name: &str) -> Work {
        Work {
            video: VideoRef::File(name.into()),
            overlays: Vec::new(),
        }
    }

    fn name(work: &Work) -> String {
        match &work.video {
            VideoRef::File(path) => path.display().to_string(),
            VideoRef::ImageSequence(_) => unreachable!(),
        }
    }

    /// What a job reported: its work, the thread it ran on and whether it was cancelled by the end.
    type Report = (String, ThreadId, bool);

    /// A runner whose job `hold` stays until released, as a read stuck on a mount would hold the worker, and says
    /// when it began; every other job reports at once. Cancelling does not end `hold`: only the release does.
    fn runner(
        hold: &'static str,
    ) -> (
        Runner,
        mpsc::Sender<()>,
        mpsc::Receiver<()>,
        mpsc::Receiver<Report>,
    ) {
        let (release, released) = mpsc::channel::<()>();
        let (begun, began) = mpsc::channel::<()>();
        let (log, seen) = mpsc::channel::<Report>();
        let released = Mutex::new(released);
        let run: Runner = Arc::new(move |work, cancel| {
            if name(work) == hold {
                begun.send(()).unwrap();
                released.lock().unwrap().recv_timeout(DEADLINE).unwrap();
            }
            log.send((name(work), thread::current().id(), cancel.is_cancelled()))
                .unwrap();
        });
        (run, release, began, seen)
    }

    #[test]
    fn a_new_request_replaces_the_one_that_waits_and_cancels_the_one_that_runs_all_on_one_thread() {
        let (run, release, began, seen) = runner("a");
        let preparer = Preparer::with_runner(run);

        let _a = preparer.start(work("a"));
        began.recv_timeout(DEADLINE).unwrap();
        // The worker is held by `a`, so both requests are made while it runs and `c` replaces `b` in the slot.
        let _b = preparer.start(work("b"));
        let _c = preparer.start(work("c"));
        assert!(
            preparer
                .shared
                .lock()
                .running
                .as_ref()
                .is_some_and(CancelToken::is_cancelled),
            "the next request cancels the job that runs"
        );
        release.send(()).unwrap();

        let (first, thread_a, cancelled) = seen.recv_timeout(DEADLINE).unwrap();
        assert_eq!((first.as_str(), cancelled), ("a", true));
        let (second, thread_c, cancelled) = seen.recv_timeout(DEADLINE).unwrap();
        assert_eq!((second.as_str(), cancelled), ("c", false), "`b` never ran");
        assert_eq!(thread_a, thread_c, "one worker serves both");
    }

    #[test]
    fn a_withdrawn_request_is_never_run() {
        let (run, release, began, seen) = runner("hold");
        let preparer = Preparer::with_runner(run);

        let _hold = preparer.start(work("hold"));
        began.recv_timeout(DEADLINE).unwrap();
        // Waiting in the slot, then withdrawn by its viewer, and replaced by another.
        drop(preparer.start(work("gone")));
        let _kept = preparer.start(work("kept"));
        release.send(()).unwrap();

        let ran: Vec<String> = (0..2)
            .map(|_| seen.recv_timeout(DEADLINE).unwrap().0)
            .collect();
        assert_eq!(ran, ["hold", "kept"]);

        // A request withdrawn while it is the one in the slot leaves nothing to run.
        let (run, release, began, seen) = runner("hold");
        let preparer = Preparer::with_runner(run);
        let _hold = preparer.start(work("hold"));
        began.recv_timeout(DEADLINE).unwrap();
        drop(preparer.start(work("gone")));
        release.send(()).unwrap();
        assert_eq!(seen.recv_timeout(DEADLINE).unwrap().0, "hold");
        // The preparer stays up until the worker has taken the withdrawn request and is idle again: a request
        // that was taken and run reported before the worker went idle, and one stop() cleared would not count.
        let until = Instant::now() + DEADLINE;
        while {
            let state = preparer.shared.lock();
            state.pending.is_some() || state.running.is_some()
        } {
            assert!(Instant::now() < until, "the worker did not drain the slot");
            thread::yield_now();
        }
        assert!(seen.try_recv().is_err(), "the withdrawn request ran");
        drop(preparer);
        assert!(matches!(
            seen.recv_timeout(DEADLINE),
            Err(RecvTimeoutError::Disconnected)
        ));
    }

    #[test]
    fn the_worker_ends_when_the_preparer_goes() {
        let (alive, gone) = mpsc::channel::<()>();
        // The worker and the preparer each hold the runner, so `alive` is dropped only when both are gone.
        let run: Runner = Arc::new(move |_, _| {
            let _ = alive.send(());
        });
        let preparer = Preparer::with_runner(run);
        let _ran = preparer.start(work("a"));
        gone.recv_timeout(DEADLINE).unwrap();
        drop(preparer);
        assert!(matches!(
            gone.recv_timeout(DEADLINE),
            Err(RecvTimeoutError::Disconnected)
        ));
    }
}
