//! How evenly a video's frames are spaced, read from the gaps between their container timestamps.

use foundation::MediaTime;
use std::collections::HashMap;

/// How many distinct periods are kept.
const KEPT: usize = 5;

/// The spacing of a video's frames: each gap between consecutive timestamps in ticks, converted to microseconds and
/// rounded, so a millisecond time base shows a 30 fps video as 33 and 34 ms gaps and calls it variable.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cadence {
    /// Whether more than one distinct period occurs.
    pub variable: bool,
    /// The most common periods in microseconds, most common first and in order of first appearance among equals;
    /// at most five.
    pub periods_us: Vec<i64>,
}

impl Cadence {
    /// The cadence of frames with timestamps `pts` (nondecreasing, in `time_base` seconds per tick); `None`
    /// for fewer than two frames.
    pub(super) fn of_pts(pts: &[i64], time_base: (i32, i32)) -> Option<Self> {
        // Period, then how often it occurs and where it first occurred.
        let mut seen: HashMap<i64, (usize, usize)> = HashMap::new();
        for pair in pts.windows(2) {
            let period = pair[1].checked_sub(pair[0]).filter(|ticks| *ticks > 0);
            let period = period.and_then(|ticks| {
                MediaTime::from_ticks(ticks, i64::from(time_base.0), i64::from(time_base.1))
            });
            if let Some(period) = period {
                let next = seen.len();
                seen.entry(period.as_us()).or_insert((0, next)).0 += 1;
            }
        }
        let mut ranked: Vec<_> = seen.into_iter().collect();
        ranked.sort_by_key(|(_, (count, first))| (std::cmp::Reverse(*count), *first));
        if ranked.is_empty() {
            return None;
        }
        Some(Self {
            variable: ranked.len() > 1,
            periods_us: ranked
                .iter()
                .take(KEPT)
                .map(|(period, _)| *period)
                .collect(),
        })
    }

    /// The cadence of a video whose frames are all `period` apart.
    pub(super) fn constant(period: MediaTime) -> Self {
        Self {
            variable: false,
            periods_us: vec![period.as_us()],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_millisecond_time_base_rounds_a_steady_rate_into_two_periods() {
        let cadence = Cadence::of_pts(&[0, 33, 67, 100, 133, 167, 200], (1, 1000)).expect("frames");
        assert!(cadence.variable);
        assert_eq!(cadence.periods_us, [33_000, 34_000]);
    }

    #[test]
    fn periods_are_ranked_by_count_then_first_appearance_and_at_most_five_are_kept() {
        let gaps = [1, 1, 2, 3, 5, 6, 7, 2, 2];
        let mut pts = vec![0];
        for gap in gaps {
            pts.push(pts[pts.len() - 1] + gap);
        }
        let cadence = Cadence::of_pts(&pts, (1, 1000)).expect("frames");
        // 2 occurs three times and 1 twice; the single ones follow in order of appearance, and 7 is cut off.
        assert_eq!(cadence.periods_us, [2_000, 1_000, 3_000, 5_000, 6_000]);
    }

    #[test]
    fn one_frame_has_no_cadence_and_equal_gaps_are_constant() {
        assert_eq!(Cadence::of_pts(&[7], (1, 90_000)), None);
        let cadence = Cadence::of_pts(&[0, 3_600, 7_200], (1, 90_000)).expect("frames");
        assert!(!cadence.variable);
        assert_eq!(cadence.periods_us, [40_000]);
    }
}
