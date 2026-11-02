//! How well an overlay file's samples line up with the video's frames, for a warning when they do not.

use foundation::{MediaTime, Timeline};
use scene::Coverage;

use crate::{MatchBasis, MatchKind, MatchPolicy, SampleKeys, Selection, Sticky, select};

/// The smallest gap between the last sample and the end of the video that counts as far.
const END_GAP_MIN: MediaTime = MediaTime(1_000_000);
/// The share of the video's length a gap between the last sample and its end must also exceed to count as far.
const END_GAP_SHARE: f64 = 0.05;
/// The share of the attainable samples frames must select for the file to line up with the video.
const MATCHED_ENOUGH: f64 = 0.8;

/// How well a file lines up with a video, worst first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Verdict {
    /// Fewer than 80% of the attainable samples are on a frame.
    Weak,
    /// The last sample is far from the last frame; see [`Alignment::ends_far`]. Only a match by time has one.
    EndsFar,
    /// Some samples are on no frame. A file at a higher rate than the video always has some.
    Partial,
    /// Every sample is on a frame.
    Good,
}

/// How the samples of a file meet the frames of a video under one policy.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Alignment {
    /// What the samples were matched by.
    pub basis: MatchBasis,
    /// How many samples the file has.
    pub samples: usize,
    /// How many of them some frame selects exactly or, by time, within the tolerance; sticky does not count.
    pub matched: usize,
    /// How many of them some frame selects exactly; by frame, all of those matched.
    pub exact: usize,
    /// By time, the last sample's time minus the last frame's time.
    pub end_gap: Option<MediaTime>,
    /// The last frame's number.
    pub last_frame: u64,
    /// The last frame's time.
    pub video_end: MediaTime,
}

impl Alignment {
    /// How many samples a perfectly aligned file would have selected. By time a frame selects one sample, so a file
    /// sampled faster than the video cannot have them all selected. By frame every record has a frame of its own, so a
    /// record past the video's last frame is a miss.
    pub fn attainable(&self) -> usize {
        match self.basis {
            MatchBasis::Time => {
                let frames = usize::try_from(self.last_frame)
                    .map_or(usize::MAX, |last| last.saturating_add(1));
                self.samples.min(frames)
            }
            MatchBasis::Frame => self.samples,
        }
    }

    /// Whether enough of the attainable samples are selected by a frame for the file to line up with the video.
    pub fn lines_up(&self) -> bool {
        #[expect(
            clippy::cast_precision_loss,
            reason = "a share of the samples needs no exactness"
        )]
        let share = self.matched as f64 / self.attainable().max(1) as f64;
        self.samples == 0 || share >= MATCHED_ENOUGH
    }

    /// How many samples no frame selects.
    pub fn unmatched(&self) -> usize {
        self.samples - self.matched
    }

    /// How well the file lines up, by its worst trait.
    pub fn verdict(&self) -> Verdict {
        if !self.lines_up() {
            Verdict::Weak
        } else if self.ends_far() {
            Verdict::EndsFar
        } else if self.unmatched() > 0 {
            Verdict::Partial
        } else {
            Verdict::Good
        }
    }

    /// Whether the last sample is far from the last frame: more than a second and more than 5% of the video. Usually
    /// the file and the video count time differently (for example frame number times a frame period against the
    /// container's timestamps); it can also be a file that covers part of the video on purpose.
    pub fn ends_far(&self) -> bool {
        self.end_gap.is_some_and(|gap| {
            #[expect(
                clippy::cast_precision_loss,
                clippy::cast_possible_truncation,
                reason = "a share of a video's length in microseconds needs no exactness"
            )]
            let share = (self.video_end.0 as f64 * END_GAP_SHARE) as i64;
            gap.0.unsigned_abs() > END_GAP_MIN.0.max(share).unsigned_abs()
        })
    }
}

/// How the samples behind `keys` meet the frames of `timeline` under `policy`'s basis and tolerance, by the rules of
/// [`select`] with sticky off. `policy.basis` is the settled one. Costs one selection per frame.
pub fn alignment(
    keys: &SampleKeys,
    coverage: Coverage,
    timeline: &Timeline,
    policy: &MatchPolicy,
) -> Alignment {
    let policy = MatchPolicy {
        sticky: Sticky {
            enabled: false,
            timeout: None,
        },
        ..*policy
    };
    let basis = policy.basis.unwrap_or_default();
    let mut selected = vec![None; keys.len()];
    for sequence_id in 0..timeline.len() {
        let Some(frame) = timeline.frame(sequence_id) else {
            break;
        };
        if let Selection::Sample { index, kind } = select(keys, coverage, frame, timeline, &policy)
            && kind != MatchKind::Retained
            && let Some(seen) = selected.get_mut(index)
        {
            // Exact wins: a sample one frame hits exactly and another within the tolerance is exact.
            if *seen != Some(MatchKind::Exact) {
                *seen = Some(kind);
            }
        }
    }
    let last_frame = timeline.last();
    let video_end = timeline.time(last_frame).unwrap_or_default();
    let end_gap = (basis == MatchBasis::Time)
        .then(|| {
            let times = keys.times.as_ref()?;
            let last = times.get(times.len().checked_sub(1)?)?;
            Some(MediaTime(last.0.saturating_sub(video_end.0)))
        })
        .flatten();
    Alignment {
        basis,
        samples: keys.len(),
        matched: selected.iter().filter(|seen| seen.is_some()).count(),
        exact: selected
            .iter()
            .filter(|seen| **seen == Some(MatchKind::Exact))
            .count(),
        end_gap,
        last_frame,
        video_end,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FrameKey, KeyChunks, Tolerance};

    fn ms(values: &[i64]) -> Vec<MediaTime> {
        values.iter().map(|ms| MediaTime(ms * 1000)).collect()
    }

    /// A time-keyed file: its frame keys are the samples' positions.
    fn times(keys: &[i64]) -> SampleKeys {
        SampleKeys {
            frames: Some(
                KeyChunks::new().appended(&in_order(&(0..keys.len() as u64).collect::<Vec<_>>())),
            ),
            times: Some(KeyChunks::new().appended(&ms(keys))),
        }
    }

    fn in_order(frames: &[u64]) -> Vec<FrameKey> {
        frames
            .iter()
            .enumerate()
            .map(|(sample, &frame)| FrameKey {
                frame,
                sample: sample as u32,
            })
            .collect()
    }

    fn by(basis: MatchBasis, tolerance: Tolerance) -> MatchPolicy {
        MatchPolicy {
            basis: Some(basis),
            tolerance,
            ..MatchPolicy::default()
        }
    }

    fn timeline(frames: &[i64]) -> Timeline {
        Timeline::new(ms(frames)).expect("valid frame times")
    }

    fn within_40() -> MatchPolicy {
        by(
            MatchBasis::Time,
            Tolerance::PreviousWithin(MediaTime(40_000)),
        )
    }

    #[test]
    fn a_sample_matches_when_a_frame_selects_it_exactly_or_within_the_tolerance() {
        // Frames every 40 ms with one dropped at 80 ms; samples on the even grid and one 10 ms early.
        let video = timeline(&[0, 40, 120, 160]);
        let samples = times(&[0, 40, 80, 150]);
        let exact = alignment(
            &samples,
            Coverage::Sparse,
            &video,
            &by(MatchBasis::Time, Tolerance::Exact),
        );
        assert_eq!((exact.samples, exact.matched, exact.exact), (4, 2, 2));
        assert_eq!(exact.verdict(), Verdict::Weak);
        let tolerated = alignment(&samples, Coverage::Sparse, &video, &within_40());
        assert_eq!((tolerated.matched, tolerated.exact), (4, 2));
        assert_eq!(tolerated.verdict(), Verdict::Good);
    }

    #[test]
    fn a_sample_overtaken_by_a_later_one_before_any_frame_never_matches() {
        let alignment = alignment(
            &times(&[0, 10, 20, 30]),
            Coverage::Sparse,
            &timeline(&[0, 40]),
            &within_40(),
        );
        assert_eq!(alignment.unmatched(), 2);
    }

    #[test]
    fn a_file_that_ends_far_from_the_video_says_so() {
        // A 60 s video whose overlay was timed for 1042 frames at 40 ms instead of the container's timestamps.
        let video = timeline(&[0, 30_000, 60_000]);
        let short = alignment(&times(&[0, 41_000]), Coverage::Sparse, &video, &within_40());
        assert_eq!(short.end_gap, Some(MediaTime(-19_000_000)));
        assert!(short.ends_far());
        // Its last sample is on no frame either, so half the samples are off: weak before far.
        assert_eq!(short.verdict(), Verdict::Weak);
        let on_frames = alignment(&times(&[0, 30_000]), Coverage::Sparse, &video, &within_40());
        assert_eq!(on_frames.verdict(), Verdict::EndsFar);
        let close = alignment(&times(&[0, 59_500]), Coverage::Sparse, &video, &within_40());
        assert!(!close.ends_far());
        // Under a second is never far, even for a short video.
        let brief = alignment(
            &times(&[0, 1_100]),
            Coverage::Sparse,
            &timeline(&[0, 2_000]),
            &within_40(),
        );
        assert!(!brief.ends_far());
    }

    #[test]
    fn frame_keyed_samples_match_frames_the_video_has() {
        let samples = SampleKeys {
            frames: Some(KeyChunks::new().appended(&in_order(&[0, 1, 5]))),
            times: None,
        };
        let video = timeline(&[0, 40, 80]);
        let by_frame = alignment(
            &samples,
            Coverage::Dense,
            &video,
            &by(MatchBasis::Frame, Tolerance::Exact),
        );
        assert_eq!(
            (by_frame.matched, by_frame.exact, by_frame.end_gap),
            (2, 2, None)
        );
        assert_eq!(by_frame.verdict(), Verdict::Weak);
        // Without times nothing is matched by time.
        let by_time = alignment(&samples, Coverage::Dense, &video, &within_40());
        assert_eq!(by_time.matched, 0);
    }

    #[test]
    fn a_file_at_twice_the_video_rate_can_match_no_more_than_the_frames() {
        let samples = times(&(0..8).map(|k| k * 20).collect::<Vec<_>>());
        let video = timeline(&[0, 40, 80, 120]);
        let aligned = alignment(
            &samples,
            Coverage::Sparse,
            &video,
            &by(MatchBasis::Time, Tolerance::Exact),
        );
        assert_eq!((aligned.matched, aligned.attainable()), (4, 4));
        assert!(aligned.lines_up());
    }

    #[test]
    fn records_past_the_last_frame_do_not_line_up_by_frame() {
        // 1000 records on frames 0 to 999 against 300 frames: only 300 can be selected.
        let records = SampleKeys {
            frames: Some(KeyChunks::new().appended(&in_order(&(0..1000).collect::<Vec<_>>()))),
            times: None,
        };
        let video = timeline(&(0..300).map(|k| k * 40).collect::<Vec<_>>());
        let by_frame = alignment(
            &records,
            Coverage::Dense,
            &video,
            &by(MatchBasis::Frame, Tolerance::Exact),
        );
        assert_eq!((by_frame.matched, by_frame.attainable()), (300, 1000));
        assert!(!by_frame.lines_up());
    }
}
