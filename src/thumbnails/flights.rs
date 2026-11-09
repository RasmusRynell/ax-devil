//! The reads of video pictures in progress: one per tag, shared by every request for it, run by at most
//! [`WORKERS`] at a time, and remembered for [`FAILURE_COOLDOWN`] when they fail.

use super::stopped;
use bytes::Bytes;
use foundation::{CancelToken, Interrupt, UserError};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, MutexGuard, PoisonError, Weak},
    time::{Duration, Instant},
};
use tokio::{
    sync::{Semaphore, watch},
    task::spawn_blocking,
};

/// How long a tag whose read failed answers with that failure, so that a broken file is not opened again by every
/// refresh of a card.
const FAILURE_COOLDOWN: Duration = Duration::from_secs(10);
/// How many pictures are decoded at once.
const WORKERS: usize = 2;

/// The result of one read of a video.
type Outcome = Result<Bytes, UserError>;

/// What is known of a tag that is being read or whose read failed.
enum Slot {
    /// Being read; the reads that wait for it hold the [`Flight`].
    Running(Weak<Flight>),
    /// Failed at `at`.
    Failed { error: UserError, at: Instant },
}

/// One read of a video, shared by the requests waiting for it. Dropping the last holder cancels the read.
pub(super) struct Flight {
    cancel: CancelToken,
    outcome: watch::Receiver<Option<Outcome>>,
}

impl Drop for Flight {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}

impl Flight {
    /// Waits for the read to end.
    pub(super) async fn outcome(&self) -> Outcome {
        match self.outcome.clone().wait_for(Option::is_some).await {
            Ok(outcome) => outcome
                .clone()
                .unwrap_or_else(|| Err(stopped("no outcome"))),
            Err(_) => Err(stopped("the read ended without an outcome")),
        }
    }
}

/// The reads in progress by tag, and the failures that are still remembered.
#[derive(Clone)]
pub(super) struct Flights {
    slots: Arc<Mutex<HashMap<String, Slot>>>,
    /// Admission to decoding: a permit lasts as long as the read runs.
    workers: Arc<Semaphore>,
}

impl Default for Flights {
    fn default() -> Self {
        Self {
            slots: Arc::default(),
            workers: Arc::new(Semaphore::new(WORKERS)),
        }
    }
}

impl Flights {
    fn lock(&self) -> MutexGuard<'_, HashMap<String, Slot>> {
        self.slots.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The read of `tag`: the one in progress, a remembered failure, or `work`, started on a blocking thread once one
    /// of the [`WORKERS`] is free. The read is cancelled when every holder of the returned flight has dropped it, and
    /// a cancelled read that has not started never does.
    pub(super) fn join(
        &self,
        tag: &str,
        work: impl FnOnce(&CancelToken) -> Result<Bytes, Interrupt<UserError>> + Send + 'static,
    ) -> Arc<Flight> {
        let mut slots = self.lock();
        slots.retain(|_, slot| match slot {
            Slot::Running(flight) => flight.strong_count() > 0,
            Slot::Failed { at, .. } => at.elapsed() < FAILURE_COOLDOWN,
        });
        match slots.get(tag) {
            Some(Slot::Running(flight)) => {
                if let Some(flight) = flight.upgrade() {
                    return flight;
                }
            }
            Some(Slot::Failed { error, .. }) => {
                let (_, outcome) = watch::channel(Some(Err(error.clone())));
                return Arc::new(Flight {
                    cancel: CancelToken::new(),
                    outcome,
                });
            }
            None => {}
        }
        let cancel = CancelToken::new();
        let (sender, outcome) = watch::channel(None);
        let flight = Arc::new(Flight {
            cancel: cancel.clone(),
            outcome,
        });
        let weak = Arc::downgrade(&flight);
        slots.insert(tag.to_owned(), Slot::Running(weak.clone()));
        drop(slots);
        let flights = self.clone();
        let tag = tag.to_owned();
        let workers = Arc::clone(&self.workers);
        drop(tokio::spawn(async move {
            // The semaphore is never closed, so waiting for a permit cannot fail.
            let Ok(permit) = workers.acquire_owned().await else {
                return;
            };
            let _ = spawn_blocking(move || {
                let _permit = permit;
                let outcome = match cancel.check() {
                    Ok(()) => match work(&cancel) {
                        Ok(jpeg) => Some(Ok(jpeg)),
                        Err(Interrupt::Failed(error)) => Some(Err(error)),
                        Err(Interrupt::Cancelled) => None,
                    },
                    Err(_) => None,
                };
                flights.finish(&tag, &weak, outcome.as_ref().and_then(|o| o.as_ref().err()));
                if outcome.is_some() {
                    sender.send_replace(outcome);
                }
            })
            .await;
        }));
        flight
    }

    /// Ends the slot of `tag` that belongs to `flight`: it remembers `failure`, or is removed. A newer flight of
    /// the same tag is left alone.
    fn finish(&self, tag: &str, flight: &Weak<Flight>, failure: Option<&UserError>) {
        let mut slots = self.lock();
        if !matches!(slots.get(tag), Some(Slot::Running(running)) if running.ptr_eq(flight)) {
            return;
        }
        match failure {
            Some(error) => {
                let error = error.clone();
                slots.insert(
                    tag.to_owned(),
                    Slot::Failed {
                        error,
                        at: Instant::now(),
                    },
                );
            }
            None => {
                slots.remove(tag);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::ErrorCode;
    use std::sync::{
        atomic::{AtomicU64, Ordering},
        mpsc,
    };

    type Work = Box<dyn FnOnce(&CancelToken) -> Result<Bytes, Interrupt<UserError>> + Send>;

    /// Work that waits for the sender, counts its runs and hands out its token once it has started.
    fn held_work(
        runs: &Arc<AtomicU64>,
        outcome: Outcome,
    ) -> (Work, mpsc::Receiver<CancelToken>, mpsc::Sender<()>) {
        let (started, token) = mpsc::channel();
        let (release, released) = mpsc::channel();
        let runs = Arc::clone(runs);
        let work = move |cancel: &CancelToken| {
            runs.fetch_add(1, Ordering::SeqCst);
            started.send(cancel.clone()).unwrap();
            released.recv().ok();
            outcome.map_err(Interrupt::Failed)
        };
        (Box::new(work), token, release)
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn requests_for_a_tag_share_one_read_which_stops_with_the_last_of_them() {
        let flights = Flights::default();
        let runs = Arc::new(AtomicU64::new(0));

        // Two joins run the work once; one leaving does not stop it.
        let (work, token, release) = held_work(&runs, Ok(Bytes::from_static(b"jpeg")));
        let first = flights.join("shared", work);
        let second = flights.join("shared", |_| panic!("the read is already running"));
        let cancel = token.recv().unwrap();
        drop(first);
        assert!(!cancel.is_cancelled());
        release.send(()).unwrap();
        assert_eq!(second.outcome().await.unwrap(), Bytes::from_static(b"jpeg"));
        assert_eq!(runs.load(Ordering::SeqCst), 1);

        // The last one leaving stops it.
        let (work, token, release) = held_work(&runs, Ok(Bytes::new()));
        let only = flights.join("left", work);
        let cancel = token.recv().unwrap();
        drop(only);
        assert!(cancel.is_cancelled());
        release.send(()).unwrap();

        // A failure reaches every waiter and is returned again during the cooldown without a new read.
        let error = UserError::new(ErrorCode::UnsupportedFormat, "broken");
        let (work, token, release) = held_work(&runs, Err(error.clone()));
        let waiting = [
            flights.join("broken", work),
            flights.join("broken", |_| panic!()),
        ];
        token.recv().unwrap();
        release.send(()).unwrap();
        for flight in &waiting {
            assert_eq!(flight.outcome().await, Err(error.clone()));
        }
        let again = flights.join("broken", |_| panic!("the failure is remembered"));
        assert_eq!(again.outcome().await, Err(error));
        assert_eq!(runs.load(Ordering::SeqCst), 3);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn only_the_workers_decode_at_once_and_a_read_left_waiting_is_never_started() {
        let flights = Flights::default();
        let runs = Arc::new(AtomicU64::new(0));
        let mut held = Vec::new();
        for tag in ["a", "b", "c", "d"] {
            let (work, token, release) = held_work(&runs, Ok(Bytes::new()));
            held.push((tag, flights.join(tag, work), token, release));
        }
        let mut third = held.split_off(WORKERS);
        for (_, _, token, _) in &held {
            token.recv().unwrap();
        }
        // The other two wait for a free worker; one has no one left to answer.
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert_eq!(runs.load(Ordering::SeqCst), 2);
        drop(third.pop());

        // A worker that finishes lets the next read start, and the abandoned one is skipped.
        let (_, _, _, release) = &held[0];
        release.send(()).unwrap();
        let (_, _, token, release) = &third[0];
        token.recv_timeout(Duration::from_secs(5)).unwrap();
        release.send(()).unwrap();
        assert_eq!(third[0].1.outcome().await, Ok(Bytes::new()));
        assert_eq!(runs.load(Ordering::SeqCst), 3);
    }
}
