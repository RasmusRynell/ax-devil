//! The live gate: encoded video units wait until their overlay is settled, or for a bounded time.
//!
//! Pure and clock-free: the caller passes the time on every call, so the same calls always give the same releases.
//! A frame leaves when the live track has decided what it shows (a future sample can never appear early) or when it has
//! waited the deadline, whichever comes first. Units leave in arrival order, because they are decoded in that order.

use std::{collections::VecDeque, time::Duration};

use foundation::{FrameId, MediaTime};

/// How far before a frame a live sample may be and still belong to it: the window is `[t - 10 ms, t]`, ends included.
pub const LIVE_TOLERANCE: MediaTime = MediaTime(10_000);

/// How long a frame waits for its overlay: the start value of decision D28, until camera skew is measured.
pub const LIVE_DEADLINE: Duration = Duration::from_millis(1_500);

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
}

impl<T> LiveGate<T> {
    /// A gate that holds a unit at most `deadline` and at most `bound` units (at least 1).
    pub fn new(deadline: Duration, bound: usize) -> Self {
        Self {
            deadline,
            bound: bound.max(1),
            pending: VecDeque::new(),
        }
    }

    /// Holds `unit`, the frame `id` that arrived at `now`. When that exceeds the bound, the oldest unit leaves instead.
    pub fn push(&mut self, id: FrameId, now: Mono, unit: T) -> Option<(FrameId, T)> {
        self.pending.push_back(Pending {
            id,
            arrived: now,
            unit,
        });
        if self.pending.len() > self.bound {
            return self.pending.pop_front().map(|old| (old.id, old.unit));
        }
        None
    }

    /// Takes the units that may leave at `now`, oldest first: from the front while the unit's frame is settled
    /// (`snapshot_decides`) or has waited the deadline. A unit that is neither holds back those behind it.
    pub fn release(
        &mut self,
        snapshot_decides: impl Fn(FrameId) -> bool,
        now: Mono,
    ) -> Vec<(FrameId, T)> {
        let mut released = Vec::new();
        while let Some(front) = self.pending.front() {
            if !snapshot_decides(front.id) && now < self.due(front) {
                break;
            }
            if let Some(Pending { id, unit, .. }) = self.pending.pop_front() {
                released.push((id, unit));
            }
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
    }

    fn due(&self, held: &Pending<T>) -> Mono {
        Mono(held.arrived.0 + self.deadline)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FrameKey, MatchPolicy, SampleKeys, Selection, Tolerance, select};
    use foundation::Timeline;
    use scene::{Coverage, MatchBasis};

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

    /// The frames pushed at `now` 0; the unit is the frame number.
    fn pushed(gate: &mut LiveGate<u64>, frames: impl IntoIterator<Item = u64>) {
        for n in frames {
            assert!(gate.push(id(n), at(0), n).is_none());
        }
    }

    fn units(released: Vec<(FrameId, u64)>) -> Vec<u64> {
        released.into_iter().map(|(_, unit)| unit).collect()
    }

    /// The frame at 1 s selects a sample `back` microseconds before it, with the live window; whether it does.
    fn matches_back(back: i64) -> bool {
        let video = Timeline::new(vec![MediaTime(0), MediaTime(1_000_000)]).unwrap();
        let keys = SampleKeys {
            frames: Some(
                [FrameKey {
                    frame: 0,
                    sample: 0,
                }]
                .into_iter()
                .collect(),
            ),
            times: Some([MediaTime(1_000_000 - back)].into_iter().collect()),
        };
        let policy = MatchPolicy {
            basis: Some(MatchBasis::Time),
            tolerance: Tolerance::PreviousWithin(LIVE_TOLERANCE),
            sticky: crate::Sticky {
                enabled: false,
                timeout: None,
            },
        };
        let frame = FrameId {
            sequence_id: 1,
            timestamp: MediaTime(1_000_000),
        };
        matches!(
            select(&keys, Coverage::Sparse, frame, &video, &policy),
            Selection::Sample { .. }
        )
    }

    #[test]
    fn the_live_gate() {
        let cases: Vec<(&str, fn())> = vec![
            (
                "released when decided, in order, ahead of the deadline",
                || {
                    let mut gate = gate(10);
                    pushed(&mut gate, 0..3);
                    assert!(gate.release(|_| false, at(100)).is_empty());
                    let settled = |f: FrameId| f.sequence_id < 2;
                    assert_eq!(units(gate.release(settled, at(100))), [0, 1]);
                    assert_eq!(gate.next_deadline(), Some(at(1_500)));
                },
            ),
            ("released at the deadline, edge included", || {
                let mut gate = gate(10);
                pushed(&mut gate, 0..2);
                assert!(gate.release(|_| false, at(1_499)).is_empty());
                assert_eq!(units(gate.release(|_| false, at(1_500))), [0, 1]);
                assert_eq!(gate.next_deadline(), None);
            }),
            ("the live window includes both ends", || {
                assert!(matches_back(0));
                assert!(matches_back(LIVE_TOLERANCE.0));
                assert!(!matches_back(LIVE_TOLERANCE.0 + 1));
            }),
            ("out-of-order arrival waits for the unit in front", || {
                let mut gate = gate(10);
                pushed(&mut gate, [2, 1]);
                let settled = |f: FrameId| f.sequence_id == 2;
                assert_eq!(units(gate.release(settled, at(10))), [2]);
                assert_eq!(units(gate.release(|_| false, at(1_500))), [1]);
            }),
            (
                "over the bound the oldest leaves at once, so a fast quiet stream keeps flowing",
                || {
                    let mut gate = gate(4);
                    pushed(&mut gate, 0..4);
                    assert_eq!(gate.push(id(4), at(0), 4).map(|(_, unit)| unit), Some(0));
                    assert!(gate.release(|_| false, at(0)).is_empty());
                    // 120 frames a second for 10 s with an overlay that never decides: frames leave throughout.
                    let mut gate = LiveGate::new(LIVE_DEADLINE, 120);
                    let mut left = 0;
                    for n in 0..1_200u64 {
                        let now = Mono(Duration::from_micros(n * 1_000_000 / 120));
                        left += usize::from(gate.push(id(n), now, n).is_some());
                        left += gate.release(|_| false, now).len();
                    }
                    assert_eq!(left, 1_200 - 120);
                },
            ),
            ("reset drops everything and restarts", || {
                let mut gate = gate(3);
                pushed(&mut gate, [1, 2, 3]);
                gate.reset();
                assert_eq!(gate.next_deadline(), None);
                pushed(&mut gate, [1]);
                assert_eq!(units(gate.release(|_| true, at(0))), [1]);
            }),
        ];
        for (name, case) in cases {
            std::panic::catch_unwind(case).unwrap_or_else(|_| panic!("case failed: {name}"));
        }
    }
}
