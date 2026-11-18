use std::sync::Arc;

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Media Time: microseconds from the first frame of a video. Always comes from container timestamps, never from a
/// frame rate.
#[derive(
    Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
)]
#[serde(transparent)]
pub struct MediaTime(pub i64);

impl MediaTime {
    /// Time zero, the first frame.
    pub const ZERO: MediaTime = MediaTime(0);

    /// Builds a time from microseconds.
    pub const fn from_us(us: i64) -> Self {
        Self(us)
    }

    /// The time in microseconds.
    pub const fn as_us(self) -> i64 {
        self.0
    }

    /// `ticks * num / den` seconds as microseconds, ties to even. `None` when `den` is 0 or the result does not fit
    /// in an `i64`.
    pub fn from_ticks(ticks: i64, num: i64, den: i64) -> Option<Self> {
        if den == 0 {
            return None;
        }
        let mut numerator = i128::from(ticks)
            .checked_mul(i128::from(num))?
            .checked_mul(1_000_000)?;
        let mut denominator = i128::from(den);
        if denominator < 0 {
            numerator = -numerator;
            denominator = -denominator;
        }
        let mut quotient = numerator.div_euclid(denominator);
        let doubled_remainder = numerator.rem_euclid(denominator) * 2;
        if doubled_remainder > denominator
            || (doubled_remainder == denominator && quotient % 2 != 0)
        {
            quotient += 1;
        }
        i64::try_from(quotient).ok().map(Self)
    }
}

/// Frames per second as an exact ratio, both parts positive and in lowest terms.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct FrameRate {
    num: u32,
    den: u32,
}

impl FrameRate {
    /// The rate `num / den`; `None` when either is 0.
    pub fn new(num: u32, den: u32) -> Option<Self> {
        if num == 0 || den == 0 {
            return None;
        }
        let divisor = gcd(num, den);
        Some(Self {
            num: num / divisor,
            den: den / divisor,
        })
    }

    /// Parses `30`, `29.97` or `30000/1001` exactly: decimal digits become a power-of-ten denominator. `None` for
    /// anything else, zero, or a value that does not fit.
    pub fn parse(text: &str) -> Option<Self> {
        let text = text.trim();
        if let Some((num, den)) = text.split_once('/') {
            return Self::new(digits(num)?.try_into().ok()?, digits(den)?.try_into().ok()?);
        }
        let (whole, fraction) = text.split_once('.').unwrap_or((text, ""));
        let den = 10u64.checked_pow(u32::try_from(fraction.len()).ok()?)?;
        let num = digits(&format!("{whole}{fraction}"))?;
        Self::new(num.try_into().ok()?, den.try_into().ok()?)
    }

    /// Frames per second, numerator.
    pub fn num(self) -> u32 {
        self.num
    }

    /// Frames per second, denominator.
    pub fn den(self) -> u32 {
        self.den
    }

    /// Time of frame `index`: `index * den / num` seconds as microseconds, ties to even like every other
    /// [`MediaTime::from_ticks`] time. `None` when it does not fit.
    pub fn time_of(self, index: u64) -> Option<MediaTime> {
        MediaTime::from_ticks(
            i64::try_from(index).ok()?,
            i64::from(self.den),
            i64::from(self.num),
        )
    }
}

/// Only ASCII digits, as a number; `None` when empty, not digits, or too large.
fn digits(text: &str) -> Option<u64> {
    if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}

fn gcd(mut a: u32, mut b: u32) -> u32 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// Width and height of a decoded picture in pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, TS)]
#[ts(rename = "SizeWire")]
pub struct FrameSize {
    /// Pixels across.
    pub width: u32,
    /// Pixels down.
    pub height: u32,
}

/// Which frame of a video: its place in presentation order and its time.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct FrameId {
    /// Position in presentation order, starting at 0.
    pub sequence_id: u64,
    /// Presentation time of the frame.
    pub timestamp: MediaTime,
}

/// Why a timeline or its keyframes were rejected.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TimelineError {
    /// There are no frames or keyframes.
    Empty,
    /// The first frame is not at time 0, or the first keyframe is not frame 0.
    FirstNotZero,
    /// A time decreases, or a keyframe is not greater than the one before it. Carries the offending frame's
    /// sequence id, or the offending keyframe's own value.
    NotIncreasing {
        /// The offending frame or keyframe.
        sequence_id: u64,
    },
    /// A keyframe is not a frame of the video.
    KeyframeOutOfRange {
        /// The offending keyframe.
        sequence_id: u64,
    },
}

/// Presentation times by sequence id: non-empty, `times[0] == 0`, nondecreasing. Cheap to clone.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Timeline {
    times: Arc<[MediaTime]>,
}

impl Timeline {
    /// Validates `times` and wraps them.
    pub fn new(times: Vec<MediaTime>) -> Result<Self, TimelineError> {
        let Some(first) = times.first() else {
            return Err(TimelineError::Empty);
        };
        if *first != MediaTime::ZERO {
            return Err(TimelineError::FirstNotZero);
        }
        if let Some(index) = times.windows(2).position(|pair| pair[1] < pair[0]) {
            return Err(TimelineError::NotIncreasing {
                sequence_id: index as u64 + 1,
            });
        }
        Ok(Self {
            times: times.into(),
        })
    }

    /// Number of frames; at least 1.
    #[expect(clippy::len_without_is_empty, reason = "a timeline is never empty")]
    pub fn len(&self) -> u64 {
        self.times.len() as u64
    }

    /// Sequence id of the last frame.
    pub fn last(&self) -> u64 {
        self.len() - 1
    }

    /// The time of a frame, or `None` past the end.
    pub fn time(&self, sequence_id: u64) -> Option<MediaTime> {
        self.times.get(usize::try_from(sequence_id).ok()?).copied()
    }

    /// The identity of a frame, or `None` past the end.
    pub fn frame(&self, sequence_id: u64) -> Option<FrameId> {
        self.time(sequence_id).map(|timestamp| FrameId {
            sequence_id,
            timestamp,
        })
    }

    /// Last frame whose time is at or before `t`; `None` when `t` is before the first frame.
    pub fn at_or_before(&self, t: MediaTime) -> Option<u64> {
        let count = self.times.partition_point(|time| *time <= t);
        count.checked_sub(1).map(|index| index as u64)
    }

    /// Time to the next frame; for the last frame, the previous gap; `None` for a one-frame video or a sequence id
    /// past the end.
    pub fn period_after(&self, sequence_id: u64) -> Option<MediaTime> {
        let index = usize::try_from(sequence_id).ok()?;
        let (earlier, later) = if index + 1 < self.times.len() {
            (index, index + 1)
        } else if index + 1 == self.times.len() && index > 0 {
            (index - 1, index)
        } else {
            return None;
        };
        Some(MediaTime(self.times[later].0 - self.times[earlier].0))
    }

    /// Last time plus the last frame's own period, or the last time for a one-frame video.
    pub fn duration(&self) -> MediaTime {
        let last = self.times[self.times.len() - 1];
        let period = self.period_after(self.last()).unwrap_or(MediaTime::ZERO);
        MediaTime(last.0.saturating_add(period.0))
    }

    /// All times in sequence order.
    pub fn as_slice(&self) -> &[MediaTime] {
        &self.times
    }
}

/// Sequence ids where decoding can start: sorted, strictly increasing, first is 0 (stream start), all below the
/// frame count.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Keyframes {
    sequence_ids: Arc<[u64]>,
    frame_count: u64,
}

impl Keyframes {
    /// Validates `sequence_ids` against the video's `frame_count` and wraps them.
    pub fn new(sequence_ids: Vec<u64>, frame_count: u64) -> Result<Self, TimelineError> {
        let Some(first) = sequence_ids.first() else {
            return Err(TimelineError::Empty);
        };
        if *first != 0 {
            return Err(TimelineError::FirstNotZero);
        }
        for (index, &sequence_id) in sequence_ids.iter().enumerate() {
            if index > 0 && sequence_id <= sequence_ids[index - 1] {
                return Err(TimelineError::NotIncreasing { sequence_id });
            }
            if sequence_id >= frame_count {
                return Err(TimelineError::KeyframeOutOfRange { sequence_id });
            }
        }
        Ok(Self {
            sequence_ids: sequence_ids.into(),
            frame_count,
        })
    }

    /// The last keyframe at or before `sequence_id`; the last keyframe when it is past them all.
    pub fn at_or_before(&self, sequence_id: u64) -> u64 {
        let count = self
            .sequence_ids
            .partition_point(|keyframe| *keyframe <= sequence_id);
        self.sequence_ids[count - 1]
    }

    /// The first keyframe after `sequence_id`.
    pub fn after(&self, sequence_id: u64) -> Option<u64> {
        let count = self
            .sequence_ids
            .partition_point(|keyframe| *keyframe <= sequence_id);
        self.sequence_ids.get(count).copied()
    }

    /// Last sequence id decoded from `keyframe` before the next keyframe, or the last frame of the video.
    pub fn group_end(&self, keyframe: u64) -> u64 {
        self.after(keyframe)
            .map_or(self.frame_count - 1, |next| next - 1)
    }

    /// All keyframes in order.
    pub fn as_slice(&self) -> &[u64] {
        &self.sequence_ids
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Times are container timestamps scaled to microseconds, rounded half to even, relative to the first frame.
    #[test]
    fn times_come_from_pts_ticks_rounded_half_to_even() {
        // (ticks, num, den, expected µs); den 0 and overflow have no time.
        let cases = [
            (0, 1, 90_000, Some(0)),
            (3_000, 1, 90_000, Some(33_333)),
            (1, 1, 3, Some(333_333)),
            (2, 1, 3, Some(666_667)),
            (1, 1, 2_000_000, Some(0)),
            (3, 1, 2_000_000, Some(2)),
            (5, 1, 2_000_000, Some(2)),
            (-1, 1, 2_000_000, Some(0)),
            (-3, 1, 2_000_000, Some(-2)),
            (3, -1, -2_000_000, Some(2)),
            // The product exceeds i64 before the division brings it back.
            (
                9_000_000_000_000_000,
                1,
                1_000_000_000,
                Some(9_000_000_000_000),
            ),
            (1, 1, 0, None),
            (i64::MAX, 1, 1, None),
        ];
        for (ticks, num, den, expected) in cases {
            assert_eq!(
                MediaTime::from_ticks(ticks, num, den),
                expected.map(MediaTime),
                "{ticks} * {num} / {den}"
            );
        }
    }

    #[test]
    fn first_frame_is_time_zero_when_the_stream_starts_at_an_offset() {
        let pts = [900_000_i64, 903_003, 906_006];
        let first = pts[0];
        let times: Option<Vec<_>> = pts
            .iter()
            .map(|pts| MediaTime::from_ticks(pts - first, 1, 90_000))
            .collect();
        let timeline = Timeline::new(times.unwrap()).unwrap();
        assert_eq!(timeline.as_slice(), times_of(&[0, 33_367, 66_733]));
    }

    #[test]
    fn duration_is_the_last_time_plus_its_own_period() {
        // (times, duration): a variable frame rate ends with its last gap, a single frame lasts 0.
        let cases = [
            (vec![0, 40_000, 70_000, 100_000, 150_000], 200_000),
            (vec![0, 50], 100),
            (vec![0], 0),
        ];
        for (us, duration) in cases {
            let timeline = Timeline::new(times_of(&us)).unwrap();
            assert_eq!(timeline.duration(), MediaTime(duration), "{us:?}");
        }
    }

    #[test]
    fn lookups_find_the_frame_at_or_before_a_time_and_each_frames_period() {
        let timeline = Timeline::new(times_of(&[0, 10, 25, 100])).unwrap();
        assert_eq!((timeline.len(), timeline.last()), (4, 3));

        // (time, frame): before zero, exact, between, after the last.
        let at_or_before = [
            (-1, None),
            (0, Some(0)),
            (9, Some(0)),
            (10, Some(1)),
            (24, Some(1)),
            (25, Some(2)),
            (99, Some(2)),
            (100, Some(3)),
            (1_000_000, Some(3)),
        ];
        for (t, frame) in at_or_before {
            assert_eq!(timeline.at_or_before(MediaTime(t)), frame, "at {t}");
        }

        // The last frame repeats the previous gap; past the end there is no frame.
        let period_after = [
            (0, Some(10)),
            (1, Some(15)),
            (2, Some(75)),
            (3, Some(75)),
            (4, None),
        ];
        for (frame, period) in period_after {
            assert_eq!(
                timeline.period_after(frame),
                period.map(MediaTime),
                "after {frame}"
            );
        }
        assert_eq!(Timeline::new(times_of(&[0])).unwrap().period_after(0), None);

        assert_eq!(
            timeline.frame(2),
            Some(FrameId {
                sequence_id: 2,
                timestamp: MediaTime(25)
            })
        );
        assert_eq!(
            (timeline.time(3), timeline.time(4)),
            (Some(MediaTime(100)), None)
        );
    }

    #[test]
    fn timeline_rejects_times_that_are_not_a_nondecreasing_run_from_zero() {
        let cases = [
            (vec![], TimelineError::Empty),
            (vec![5, 10], TimelineError::FirstNotZero),
            (vec![-1, 0], TimelineError::FirstNotZero),
            (
                vec![0, 10, 20, 5],
                TimelineError::NotIncreasing { sequence_id: 3 },
            ),
        ];
        for (us, error) in cases {
            assert_eq!(Timeline::new(times_of(&us)), Err(error), "{us:?}");
        }
    }

    #[test]
    fn keyframe_lookups_find_where_decoding_starts_and_where_its_group_ends() {
        let keyframes = Keyframes::new(vec![0, 4, 8], 12).unwrap();
        // (frame, keyframe at or before, next keyframe).
        let cases = [
            (0, 0, Some(4)),
            (3, 0, Some(4)),
            (4, 4, Some(8)),
            (7, 4, Some(8)),
            (8, 8, None),
            (11, 8, None),
        ];
        for (frame, before, after) in cases {
            assert_eq!(
                keyframes.at_or_before(frame),
                before,
                "at or before {frame}"
            );
            assert_eq!(keyframes.after(frame), after, "after {frame}");
        }
        assert_eq!(
            [0, 4, 8].map(|keyframe| keyframes.group_end(keyframe)),
            [3, 7, 11]
        );
        assert_eq!(keyframes.as_slice(), [0, 4, 8]);
    }

    #[test]
    fn keyframes_reject_ids_that_are_not_a_valid_set_of_decode_starts() {
        let cases = [
            (vec![], 12, TimelineError::Empty),
            (vec![4, 8], 12, TimelineError::FirstNotZero),
            (
                vec![0, 4, 4],
                12,
                TimelineError::NotIncreasing { sequence_id: 4 },
            ),
            (
                vec![0, 8, 4],
                12,
                TimelineError::NotIncreasing { sequence_id: 4 },
            ),
            (
                vec![0, 12],
                12,
                TimelineError::KeyframeOutOfRange { sequence_id: 12 },
            ),
            (
                vec![0],
                0,
                TimelineError::KeyframeOutOfRange { sequence_id: 0 },
            ),
        ];
        for (ids, frame_count, error) in cases {
            assert_eq!(
                Keyframes::new(ids.clone(), frame_count),
                Err(error),
                "{ids:?}"
            );
        }
    }

    #[test]
    fn frame_rates_parse_exactly_and_place_frames_on_a_grid() {
        // (text, (num, den)); None when the text is not a positive rate.
        let cases = [
            ("30", Some((30, 1))),
            ("25.0", Some((25, 1))),
            ("29.97", Some((2997, 100))),
            ("0.5", Some((1, 2))),
            (".5", Some((1, 2))),
            ("30000/1001", Some((30000, 1001))),
            ("60/2", Some((30, 1))),
            (" 24 ", Some((24, 1))),
            ("0", None),
            ("30/0", None),
            ("", None),
            (".", None),
            ("-30", None),
            ("1e3", None),
            ("1.2.3", None),
            ("4294967296", None),
        ];
        for (text, expected) in cases {
            let parsed = FrameRate::parse(text).map(|rate| (rate.num(), rate.den()));
            assert_eq!(parsed, expected, "{text:?}");
        }

        // (rate, frame, µs); ties to even, like container timestamps.
        let times = [
            ("30", 0, 0),
            ("30", 1, 33_333),
            ("30", 3, 100_000),
            ("30000/1001", 30, 1_001_000),
            ("30000/1001", 1, 33_367),
            ("0.5", 3, 6_000_000),
        ];
        for (text, index, us) in times {
            let rate = FrameRate::parse(text).expect("a valid rate");
            assert_eq!(
                rate.time_of(index),
                Some(MediaTime(us)),
                "{text} frame {index}"
            );
        }
        assert_eq!(FrameRate::new(0, 1), None);
        assert_eq!(FrameRate::new(60, 2), FrameRate::new(30, 1));
    }

    fn times_of(us: &[i64]) -> Vec<MediaTime> {
        us.iter().copied().map(MediaTime).collect()
    }
}
