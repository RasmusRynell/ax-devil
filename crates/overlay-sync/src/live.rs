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
    keyframe: bool,
    unit: T,
}

/// Encoded units held back until their overlay is settled or they have waited out the deadline.
///
/// It holds at most `bound` units. Past that it drops the oldest and then everything up to the next keyframe, which
/// the decoder could not use alone, and counts each drop; until a keyframe arrives, units that follow a dropped one
/// are dropped too.
pub struct LiveGate<T> {
    deadline: Duration,
    bound: usize,
    pending: VecDeque<Pending<T>>,
    resync: bool,
    dropped: u64,
}

impl<T> LiveGate<T> {
    /// A gate that holds a unit at most `deadline` and at most `bound` units (at least 1).
    pub fn new(deadline: Duration, bound: usize) -> Self {
        Self {
            deadline,
            bound: bound.max(1),
            pending: VecDeque::new(),
            resync: false,
            dropped: 0,
        }
    }

    /// Holds `unit`, the frame `id` that arrived at `now`; `keyframe` says whether decoding can start at it.
    pub fn push(&mut self, id: FrameId, now: Mono, keyframe: bool, unit: T) {
        if self.resync {
            if !keyframe {
                self.dropped += 1;
                return;
            }
            self.resync = false;
        }
        self.pending.push_back(Pending {
            id,
            arrived: now,
            keyframe,
            unit,
        });
        if self.pending.len() > self.bound {
            self.pending.pop_front();
            self.dropped += 1;
            while self.pending.front().is_some_and(|front| !front.keyframe) {
                self.pending.pop_front();
                self.dropped += 1;
            }
            self.resync = self.pending.is_empty();
        }
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

    /// Drops everything held, uncounted: the stream began anew.
    pub fn reset(&mut self) {
        self.pending.clear();
        self.resync = false;
    }

    /// How many units were dropped for the bound since the gate was made.
    pub fn dropped(&self) -> u64 {
        self.dropped
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

    /// Frames 0 to 9 pushed at `now` 0, every fifth a keyframe; the unit is the frame number.
    fn pushed(gate: &mut LiveGate<u64>, frames: impl IntoIterator<Item = u64>) {
        for n in frames {
            gate.push(id(n), at(0), n % 5 == 0, n);
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
                "over the bound drops to the next keyframe and counts",
                || {
                    let mut gate = gate(4);
                    pushed(&mut gate, 0..7);
                    // Frames 0..=4 held at the fifth push; 5 pushes it over: 0 to 4 go, 5 (a keyframe) stays.
                    assert_eq!(gate.dropped(), 5);
                    assert_eq!(units(gate.release(|_| true, at(0))), [5, 6]);
                },
            ),
            (
                "with no keyframe left, units are dropped until one arrives",
                || {
                    let mut gate = gate(2);
                    pushed(&mut gate, [1, 2, 3, 4]);
                    assert_eq!(gate.dropped(), 4);
                    pushed(&mut gate, [5]);
                    assert_eq!(units(gate.release(|_| true, at(0))), [5]);
                },
            ),
            ("reset drops everything and restarts", || {
                let mut gate = gate(2);
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
