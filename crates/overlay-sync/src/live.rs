//! The live gate: encoded video units wait until their overlay is settled, or for a bounded time, and leave paced by
//! the video.
//!
//! Pure and clock-free: the caller passes the time on every call, so the same calls always give the same releases.
//! A frame may leave once the live track has decided what it shows (a future sample can never appear early) or it has
//! waited the deadline. Settled frames leave one per frame that arrives, never in a burst a viewer would show only the
//! newest of, so the delay sizes itself to how late the overlay runs. Delay the overlay has not needed for a second
//! leaves at once, one skip instead of a minute behind. Units leave in arrival order, because they are decoded in that
//! order.

use std::{collections::VecDeque, time::Duration};

use foundation::FrameId;

/// How long a frame waits for its overlay: the start value of decision D28, until camera skew is measured.
pub const LIVE_DEADLINE: Duration = Duration::from_millis(1_500);

/// How long the gate watches for delay the overlay no longer needs before it sheds it.
const SHRINK_EVERY: Duration = Duration::from_secs(1);

/// How many settled frames the gate keeps in hand against an overlay that runs a little later than before.
const SPARE: usize = 1;

/// A reading of a monotonic clock, as the time since an origin the caller chooses. Only differences mean anything.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Mono(pub Duration);

struct Pending<T> {
    id: FrameId,
    arrived: Mono,
    unit: T,
}

/// Encoded units held back until their overlay is settled or they have waited out the deadline.
///
/// It holds at most `bound` units. Past that the oldest leaves at once, as if its deadline had passed, so a stream
/// faster than `bound` per deadline keeps flowing, a little earlier than the deadline, and no unit is ever dropped.
pub struct LiveGate<T> {
    deadline: Duration,
    bound: usize,
    pending: VecDeque<Pending<T>>,
    /// Since when the gate has watched for delay to shed, and the fewest frames it held that could have left.
    window: Option<(Mono, usize)>,
}

impl<T> LiveGate<T> {
    /// A gate that holds a unit at most `deadline` and at most `bound` units (at least 1).
    pub fn new(deadline: Duration, bound: usize) -> Self {
        Self {
            deadline,
            bound: bound.max(1),
            pending: VecDeque::new(),
            window: None,
        }
    }

    /// Holds `unit`, the frame `id` that arrived at `now`, and lets out the oldest unit if it may leave (`settled` or
    /// due): one out per one in. Over the bound the oldest leaves regardless. When more than a spare frame could have
    /// left at every arrival for a second, all but the spare leave too, shedding the delay in one step.
    pub fn push(
        &mut self,
        id: FrameId,
        now: Mono,
        unit: T,
        settled: impl Fn(FrameId) -> bool,
    ) -> Vec<(FrameId, T)> {
        self.pending.push_back(Pending {
            id,
            arrived: now,
            unit,
        });
        let may_leave = |gate: &Self, held: &Pending<T>| settled(held.id) || now >= gate.due(held);
        let mut released = Vec::new();
        if self.pending.len() > self.bound
            || self
                .pending
                .front()
                .is_some_and(|front| may_leave(self, front))
        {
            released.extend(self.pop());
        }
        let ready = self
            .pending
            .iter()
            .take_while(|held| may_leave(self, held))
            .count();
        let (since, fewest) = self.window.unwrap_or((now, ready));
        let fewest = fewest.min(ready);
        if now < Mono(since.0 + SHRINK_EVERY) {
            self.window = Some((since, fewest));
        } else {
            for _ in SPARE..fewest {
                released.extend(self.pop());
            }
            self.window = None;
        }
        released
    }

    /// Takes the units that have waited the deadline at `now`, oldest first. Each is due a frame's interval after the
    /// one before, so they too leave paced by the video.
    pub fn expire(&mut self, now: Mono) -> Vec<(FrameId, T)> {
        let mut released = Vec::new();
        while self
            .pending
            .front()
            .is_some_and(|front| now >= self.due(front))
        {
            released.extend(self.pop());
        }
        released
    }

    /// When the oldest held unit reaches the deadline; `None` when nothing is held.
    pub fn next_deadline(&self) -> Option<Mono> {
        self.pending.front().map(|front| self.due(front))
    }

    /// Drops everything held: the stream began anew.
    pub fn reset(&mut self) {
        self.pending.clear();
        self.window = None;
    }

    fn pop(&mut self) -> Option<(FrameId, T)> {
        self.pending.pop_front().map(|held| (held.id, held.unit))
    }

    fn due(&self, held: &Pending<T>) -> Mono {
        Mono(held.arrived.0 + self.deadline)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::MediaTime;

    fn id(n: u64) -> FrameId {
        FrameId {
            sequence_id: n,
            timestamp: MediaTime(n as i64 * 40_000),
        }
    }

    fn at(ms: u64) -> Mono {
        Mono(Duration::from_millis(ms))
    }

    fn gate(bound: usize) -> LiveGate<u64> {
        LiveGate::new(LIVE_DEADLINE, bound)
    }

    fn units(released: Vec<(FrameId, u64)>) -> Vec<u64> {
        released.into_iter().map(|(_, unit)| unit).collect()
    }

    /// Pushes frame `n` at `now` while frames below `settled_below` are settled; the units that leave.
    fn push(gate: &mut LiveGate<u64>, n: u64, now: Mono, settled_below: u64) -> Vec<u64> {
        units(gate.push(id(n), now, n, |f| f.sequence_id < settled_below))
    }

    /// A 30 fps stream whose overlay settles each frame when the frame `late` after it arrives, from frame `from` to
    /// `to`: how many frames each push let out.
    fn run(gate: &mut LiveGate<u64>, from: u64, to: u64, late: u64) -> Vec<usize> {
        (from..to)
            .map(|n| push(gate, n, at(n * 100 / 3), (n + 1).saturating_sub(late)).len())
            .collect()
    }

    #[test]
    fn the_live_gate() {
        let cases: Vec<(&str, fn())> = vec![
            (
                "an overlay that settles three frames at once lets them out one per frame that arrives",
                || {
                    let mut gate = gate(10);
                    for n in 0..3 {
                        assert!(push(&mut gate, n, at(n * 33), 0).is_empty());
                    }
                    assert_eq!(push(&mut gate, 3, at(100), 3), [0]);
                    assert_eq!(push(&mut gate, 4, at(133), 3), [1]);
                    assert_eq!(push(&mut gate, 5, at(166), 3), [2]);
                    assert!(push(&mut gate, 6, at(200), 3).is_empty());
                },
            ),
            (
                "with nothing to wait for, each frame passes straight through",
                || {
                    let mut gate = gate(10);
                    for n in 0..100 {
                        assert_eq!(push(&mut gate, n, at(n * 33), u64::MAX), [n]);
                    }
                },
            ),
            (
                "a steady late overlay keeps its delay and never bursts",
                || {
                    let mut gate = gate(120);
                    let out = run(&mut gate, 0, 300, 4);
                    assert!(out[..4].iter().all(|n| *n == 0));
                    assert!(out[4..].iter().all(|n| *n == 1), "{out:?}");
                },
            ),
            (
                "delay a late spike left behind is shed in one step within two seconds, keeping a spare",
                || {
                    let mut gate = gate(120);
                    // The overlay runs 20 frames late, then catches up to 2.
                    run(&mut gate, 0, 100, 20);
                    let out = run(&mut gate, 100, 1_000, 2);
                    let extra: Vec<usize> = out
                        .chunks(30)
                        .map(|second| second.iter().sum::<usize>() - second.len())
                        .collect();
                    // Down to what the overlay needs and a spare, at one push.
                    assert_eq!(out.iter().filter(|n| **n > 1).count(), 1, "{out:?}");
                    assert_eq!(
                        extra[..2].iter().sum::<usize>(),
                        20 - 2 - SPARE,
                        "{extra:?}"
                    );
                    assert_eq!(gate.pending.len(), 2 + SPARE);
                },
            ),
            ("a unit leaves at its deadline, edge included", || {
                let mut gate = gate(10);
                push(&mut gate, 0, at(0), 0);
                push(&mut gate, 1, at(33), 0);
                assert!(gate.expire(at(1_499)).is_empty());
                assert_eq!(units(gate.expire(at(1_500))), [0]);
                assert_eq!(gate.next_deadline(), Some(at(1_533)));
                assert_eq!(units(gate.expire(at(1_533))), [1]);
                assert_eq!(gate.next_deadline(), None);
            }),
            (
                "a unit neither settled nor due holds those behind it",
                || {
                    let mut gate = gate(10);
                    assert!(units(gate.push(id(2), at(0), 2, |_| false)).is_empty());
                    let only_2 = |f: FrameId| f.sequence_id == 2;
                    assert_eq!(units(gate.push(id(1), at(10), 1, only_2)), [2]);
                    assert!(units(gate.push(id(3), at(20), 3, only_2)).is_empty());
                    assert_eq!(units(gate.expire(at(1_510))), [1]);
                },
            ),
            (
                "over the bound the oldest leaves at once, so a fast quiet stream keeps flowing",
                || {
                    let mut gate = gate(4);
                    for n in 0..4 {
                        assert!(push(&mut gate, n, at(0), 0).is_empty());
                    }
                    assert_eq!(push(&mut gate, 4, at(0), 0), [0]);
                    // 120 frames a second for 10 s with an overlay that never decides: frames leave throughout.
                    let mut gate = LiveGate::new(LIVE_DEADLINE, 120);
                    let mut left = 0;
                    for n in 0..1_200u64 {
                        let now = Mono(Duration::from_micros(n * 1_000_000 / 120));
                        left += push(&mut gate, n, now, 0).len();
                        left += gate.expire(now).len();
                    }
                    assert_eq!(left, 1_200 - 120);
                },
            ),
            ("reset drops everything and restarts", || {
                let mut gate = gate(3);
                for n in 1..4 {
                    push(&mut gate, n, at(0), 0);
                }
                gate.reset();
                assert_eq!(gate.next_deadline(), None);
                assert_eq!(push(&mut gate, 1, at(0), u64::MAX), [1]);
            }),
        ];
        for (name, case) in cases {
            std::panic::catch_unwind(case).unwrap_or_else(|_| panic!("case failed: {name}"));
        }
    }
}
