//! A read from a camera that is asked for again and again (a hover card's picture, a login check), answered from what
//! the last read gave until a cooldown has passed. At most one read of a key runs at a time, however long it takes,
//! and the next starts no sooner than the cooldown after the last one finished, so hovering or polling cannot hammer a
//! camera. A read goes on when the one who asked leaves: its answer is for the next asker.

use std::{
    collections::HashMap,
    future::Future,
    sync::{Arc, Mutex, MutexGuard, PoisonError},
    time::Duration,
};
use tokio::{sync::watch, time::Instant};

/// How many keys are remembered; the least recently asked for is forgotten first.
const REMEMBERED: usize = 48;

enum State<V> {
    /// A read is running; its answer arrives on the channel.
    Reading(watch::Receiver<Option<Option<V>>>),
    /// The last read finished at `at` and gave `value` (nothing when it failed).
    Done { value: Option<V>, at: Instant },
}

struct Slot<V> {
    /// Names the read that made the slot, so a read that outlives its slot cannot write into a newer one.
    read: u64,
    asked: Instant,
    state: State<V>,
}

struct Slots<V> {
    next_read: u64,
    by_key: HashMap<String, Slot<V>>,
}

/// Reads by key, at most one at a time per key and one per `fresh` after the last finished.
pub struct Throttled<V> {
    fresh: Duration,
    slots: Arc<Mutex<Slots<V>>>,
}

fn locked<V>(slots: &Mutex<Slots<V>>) -> MutexGuard<'_, Slots<V>> {
    slots.lock().unwrap_or_else(PoisonError::into_inner)
}

impl<V: Clone + Send + Sync + 'static> Throttled<V> {
    /// Reads of one key start at least `fresh` after the last one finished.
    pub fn new(fresh: Duration) -> Self {
        Self {
            fresh,
            slots: Arc::new(Mutex::new(Slots {
                next_read: 0,
                by_key: HashMap::new(),
            })),
        }
    }

    /// What `key` gives: the answer of the read that is running, else the last read's answer within the cooldown,
    /// else the answer of `read`, which runs apart from this call. A failed read answers `None`, and that is
    /// remembered for the cooldown too.
    pub async fn get<F, Fut>(&self, key: &str, read: F) -> Option<V>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Option<V>> + Send + 'static,
    {
        let mut waiting = {
            let mut slots = locked(&self.slots);
            let now = Instant::now();
            let running = slots.by_key.get_mut(key).and_then(|slot| {
                slot.asked = now;
                match &slot.state {
                    State::Reading(answer) => Some(Ok(answer.clone())),
                    State::Done { value, at } if now.duration_since(*at) < self.fresh => {
                        Some(Err(value.clone()))
                    }
                    State::Done { .. } => None,
                }
            });
            match running {
                Some(Err(value)) => return value,
                Some(Ok(answer)) => answer,
                None => self.start(&mut slots, key, read(), now),
            }
        };
        waiting
            .wait_for(Option::is_some)
            .await
            .ok()
            .and_then(|answer| answer.clone())
            .flatten()
    }

    /// Runs `reading` apart from the caller and makes the slot of `key` for it.
    fn start<Fut>(
        &self,
        slots: &mut Slots<V>,
        key: &str,
        reading: Fut,
        now: Instant,
    ) -> watch::Receiver<Option<Option<V>>>
    where
        Fut: Future<Output = Option<V>> + Send + 'static,
    {
        let read = slots.next_read;
        slots.next_read += 1;
        let (send, answer) = watch::channel(None);
        let shared = Arc::clone(&self.slots);
        let owned_key = key.to_owned();
        tokio::spawn(async move {
            let value = reading.await;
            if let Some(slot) = locked(&shared).by_key.get_mut(&owned_key)
                && slot.read == read
            {
                slot.state = State::Done {
                    value: value.clone(),
                    at: Instant::now(),
                };
            }
            // Nobody listening is fine: the answer is kept in the slot.
            let _ = send.send(Some(value));
        });
        if slots.by_key.len() >= REMEMBERED && !slots.by_key.contains_key(key) {
            let oldest = slots
                .by_key
                .iter()
                .min_by_key(|(_, slot)| slot.asked)
                .map(|(key, _)| key.clone());
            if let Some(oldest) = oldest {
                slots.by_key.remove(&oldest);
            }
        }
        slots.by_key.insert(
            key.to_owned(),
            Slot {
                read,
                asked: now,
                state: State::Reading(answer.clone()),
            },
        );
        answer
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    #[tokio::test(start_paused = true)]
    async fn a_key_is_read_once_per_cooldown_by_everyone_who_asks_and_a_leaver_loses_nothing() {
        let reads = Arc::new(AtomicUsize::new(0));
        let throttled = Throttled::new(Duration::from_secs(10));
        let read = |answer: Option<u8>| {
            let reads = Arc::clone(&reads);
            move || async move {
                reads.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_secs(1)).await;
                answer
            }
        };

        // The first asker leaves before the read ends; a second, meanwhile, shares the same read.
        let left = tokio::time::timeout(
            Duration::from_millis(100),
            throttled.get("a", read(Some(1))),
        )
        .await;
        assert!(left.is_err());
        assert_eq!(throttled.get("a", read(Some(2))).await, Some(1));
        // Within the cooldown nothing is read again, a failure included.
        assert_eq!(throttled.get("a", read(Some(3))).await, Some(1));
        assert_eq!(throttled.get("b", read(None)).await, None);
        tokio::time::advance(Duration::from_secs(5)).await;
        assert_eq!(throttled.get("b", read(Some(4))).await, None);
        assert_eq!(reads.load(Ordering::SeqCst), 2);

        tokio::time::advance(Duration::from_secs(6)).await;
        assert_eq!(throttled.get("a", read(Some(5))).await, Some(5));
        assert_eq!(reads.load(Ordering::SeqCst), 3);
    }

    /// A read that outlasts the cooldown is still the one read: asking again shares it, and the cooldown runs from
    /// when it finished.
    #[tokio::test(start_paused = true)]
    async fn a_read_longer_than_the_cooldown_is_shared_and_the_cooldown_follows_it() {
        let reads = Arc::new(AtomicUsize::new(0));
        let throttled = Throttled::new(Duration::from_secs(10));
        let slow = |answer: u8| {
            let reads = Arc::clone(&reads);
            move || async move {
                reads.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_secs(15)).await;
                Some(answer)
            }
        };

        let first = tokio::time::timeout(Duration::from_secs(12), throttled.get("a", slow(1)));
        assert!(first.await.is_err());
        // Twelve seconds in, past the cooldown, the read still runs and the second asker joins it.
        assert_eq!(throttled.get("a", slow(2)).await, Some(1));
        assert_eq!(throttled.get("a", slow(3)).await, Some(1));
        assert_eq!(reads.load(Ordering::SeqCst), 1);

        tokio::time::advance(Duration::from_secs(11)).await;
        assert_eq!(throttled.get("a", slow(4)).await, Some(4));
        assert_eq!(reads.load(Ordering::SeqCst), 2);
    }

    /// A read whose slot was forgotten and made again for the same key does not write its answer into the new slot.
    #[tokio::test(start_paused = true)]
    async fn a_read_that_lost_its_slot_cannot_overwrite_the_newer_one() {
        let throttled = Throttled::new(Duration::from_secs(10));
        let after = |seconds: u64, answer: u8| {
            move || async move {
                tokio::time::sleep(Duration::from_secs(seconds)).await;
                Some(answer)
            }
        };

        let left = tokio::time::timeout(Duration::from_millis(10), throttled.get("a", after(5, 1)));
        assert!(left.await.is_err());
        // Enough other keys push the unfinished "a" out, then "a" is asked for again.
        for other in 0..REMEMBERED {
            throttled.get(&other.to_string(), after(0, 0)).await;
        }
        assert_eq!(throttled.get("a", after(1, 2)).await, Some(2));
        // The first read finishes after the second and must not replace its answer.
        tokio::time::advance(Duration::from_secs(5)).await;
        assert_eq!(throttled.get("a", after(0, 3)).await, Some(2));
    }
}
