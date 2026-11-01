use super::*;

const MS: i64 = 1_000;

fn times(values: &[i64]) -> SampleKeys {
    SampleKeys::Times(values.iter().map(|&t| MediaTime(t)).collect())
}

fn frames(values: &[u64]) -> SampleKeys {
    SampleKeys::Frames(values.into())
}

fn timeline(times: &[i64]) -> Timeline {
    Timeline::new(times.iter().map(|&t| MediaTime(t)).collect()).unwrap()
}

/// Ten frames, 100 ms apart.
fn even() -> Timeline {
    timeline(&(0..10).map(|i| i * 100 * MS).collect::<Vec<_>>())
}

fn at(sequence_id: u64, timestamp: i64) -> FrameId {
    FrameId {
        sequence_id,
        timestamp: MediaTime(timestamp),
    }
}

fn policy(tolerance: Tolerance, sticky: Option<Option<i64>>) -> MatchPolicy {
    MatchPolicy {
        tolerance,
        sticky: match sticky {
            Some(timeout) => Sticky {
                enabled: true,
                timeout: timeout.map(MediaTime),
            },
            None => Sticky {
                enabled: false,
                ..Sticky::default()
            },
        },
    }
}

fn within(ms: i64) -> Tolerance {
    Tolerance::PreviousWithin(MediaTime(ms * MS))
}

fn picked(index: usize, kind: MatchKind) -> Selection {
    Selection::Sample { index, kind }
}

/// Selects with a sparse coverage on the `even` timeline.
fn pick(keys: &SampleKeys, frame: FrameId, policy: &MatchPolicy) -> Selection {
    select(keys, Coverage::Sparse, frame, &even(), policy)
}

#[test]
fn time_keyed_exact_match_wins_whatever_the_tolerance() {
    let keys = times(&[1_000, 2_000]);
    for tolerance in [Tolerance::Exact, within(5)] {
        let selected = pick(&keys, at(0, 1_000), &policy(tolerance, None));
        assert_eq!(selected, picked(0, MatchKind::Exact));
    }
}

#[test]
fn time_keyed_never_selects_a_sample_after_the_frame() {
    // A sample 1 ms ahead is inside the 5 ms tolerance and still not shown.
    let keys = times(&[1_280_000]);
    let sticky = policy(within(5), Some(None));
    assert_eq!(pick(&keys, at(0, 1_279_000), &sticky), Selection::None);
}

#[test]
fn time_keyed_takes_the_latest_previous_sample_within_the_tolerance() {
    let keys = times(&[1_278_000, 1_279_000, 1_281_000]);
    let selected = pick(&keys, at(0, 1_280_000), &policy(within(5), None));
    assert_eq!(selected, picked(1, MatchKind::Tolerated));
}

#[test]
fn time_keyed_tolerance_bounds() {
    let keys = times(&[1_000_000]);
    let cases = [
        (within(5), 1_005_000, picked(0, MatchKind::Tolerated)), // inclusive
        (within(5), 1_005_001, Selection::None),
        (Tolerance::Exact, 1_000_001, Selection::None),
        (
            Tolerance::default(),
            1_050_000,
            picked(0, MatchKind::Tolerated),
        ),
    ];
    for (tolerance, now, expected) in cases {
        assert_eq!(
            pick(&keys, at(0, now), &policy(tolerance, None)),
            expected,
            "{now}"
        );
    }
}

#[test]
fn time_keyed_with_no_sample_at_or_before_the_frame_selects_nothing() {
    let sticky = policy(within(5), Some(None));
    assert_eq!(
        pick(&times(&[]), at(0, 1_280_000), &sticky),
        Selection::None
    );
    assert_eq!(pick(&times(&[500]), at(0, 100), &sticky), Selection::None);
}

#[test]
fn sticky_off_selects_nothing_beyond_the_tolerance() {
    let keys = times(&[0]);
    assert_eq!(
        pick(&keys, at(1, 100 * MS), &policy(within(50), None)),
        Selection::None
    );
}

#[test]
fn sticky_timeout_is_inclusive() {
    let keys = times(&[0]);
    let sticky = policy(Tolerance::Exact, Some(Some(2_050 * MS)));
    let cases = [
        (2_049_999, picked(0, MatchKind::Retained)),
        (2_050_000, picked(0, MatchKind::Retained)),
        (2_050_001, Selection::None),
    ];
    for (now, expected) in cases {
        assert_eq!(pick(&keys, at(1, now), &sticky), expected, "{now}");
    }
}

#[test]
fn sticky_without_timeout_never_expires() {
    let sticky = policy(Tolerance::Exact, Some(None));
    let selected = pick(&times(&[0]), at(1, 10_000_000_000), &sticky);
    assert_eq!(selected, picked(0, MatchKind::Retained));
}

#[test]
fn sticky_age_is_measured_from_the_sample_not_from_what_was_shown() {
    // Frames 1 s and 2 s after the sample; the 1 s timeout expires at the second whichever was asked first.
    let keys = times(&[0]);
    let sticky = policy(Tolerance::Exact, Some(Some(1_000 * MS)));
    let (first, second) = (at(1, 1_000 * MS), at(2, 2_000 * MS));
    let forwards = [pick(&keys, first, &sticky), pick(&keys, second, &sticky)];
    let backwards = [pick(&keys, second, &sticky), pick(&keys, first, &sticky)];
    assert_eq!(forwards[0], picked(0, MatchKind::Retained));
    assert_eq!(forwards[1], Selection::None);
    assert_eq!([forwards[1], forwards[0]], backwards);
}

#[test]
fn sticky_keeps_the_latest_sample_not_an_older_one() {
    let keys = times(&[0, 300 * MS]);
    let sticky = policy(Tolerance::Exact, Some(None));
    assert_eq!(
        pick(&keys, at(5, 500 * MS), &sticky),
        picked(1, MatchKind::Retained)
    );
}

#[test]
fn a_tolerated_sample_is_opaque_and_ignores_the_sticky_timeout() {
    let keys = times(&[0]);
    let sticky = policy(within(50), Some(Some(10 * MS)));
    assert_eq!(
        pick(&keys, at(1, 30 * MS), &sticky),
        picked(0, MatchKind::Tolerated)
    );
    assert_eq!(pick(&keys, at(1, 60 * MS), &sticky), Selection::None);
}

#[test]
fn frame_keyed_exact_and_sticky_age_follow_the_video_timeline() {
    // The MOT sparse case: samples at frames 0, 3 and 5 of a video with irregular frame times.
    let timeline = timeline(&[
        0, 1_000_000, 1_900_000, 3_000_000, 4_000_000, 6_000_000, 9_000_000,
    ]);
    let keys = frames(&[0, 3, 5]);
    let sticky = policy(within(50), Some(Some(2_050 * MS)));
    let at_frame = |n: u64| at(n, timeline.time(n).unwrap().0);
    let cases = [
        (1, picked(0, MatchKind::Retained)),
        (3, picked(1, MatchKind::Exact)),
        (2, picked(0, MatchKind::Retained)), // 1.9 s old
        (4, picked(1, MatchKind::Retained)),
        (5, picked(2, MatchKind::Exact)),
        (1, picked(0, MatchKind::Retained)), // going back changes nothing
        (6, Selection::None),                // 3 s old; 5 read as 5 us would be fresh
    ];
    for (frame, expected) in cases {
        let selected = select(&keys, Coverage::Sparse, at_frame(frame), &timeline, &sticky);
        assert_eq!(selected, expected, "frame {frame}");
    }
    let off = policy(within(50), None);
    let selected = select(&keys, Coverage::Sparse, at_frame(4), &timeline, &off);
    assert_eq!(selected, Selection::None);
    let selected = select(&keys, Coverage::Sparse, at_frame(5), &timeline, &off);
    assert_eq!(selected, picked(2, MatchKind::Exact));
}

#[test]
fn frame_keyed_sparse_before_the_first_sample_selects_nothing() {
    let sticky = policy(Tolerance::Exact, Some(None));
    let selected = select(
        &frames(&[4]),
        Coverage::Sparse,
        at(2, 200 * MS),
        &even(),
        &sticky,
    );
    assert_eq!(selected, Selection::None);
}

#[test]
fn frame_keyed_dense_covers_gaps_with_empty_and_sticky_never_retains_across_them() {
    let keys = frames(&[1, 4]);
    let sticky = policy(within(50), Some(None));
    let cases = [
        (0, Selection::Empty), // before the first sample
        (1, picked(0, MatchKind::Exact)),
        (2, Selection::Empty), // a gap, although a sample is 100 ms old
        (4, picked(1, MatchKind::Exact)),
        (9, Selection::Empty), // after the last sample
    ];
    for (frame, expected) in cases {
        let selected = select(
            &keys,
            Coverage::Dense,
            at(frame, frame as i64 * 100 * MS),
            &even(),
            &sticky,
        );
        assert_eq!(selected, expected, "frame {frame}");
    }
    let none = SampleKeys::Frames(Vec::new().into());
    assert_eq!(
        select(&none, Coverage::Dense, at(0, 0), &even(), &sticky),
        Selection::Empty
    );
}

#[test]
fn coverage_means_nothing_for_time_keyed_files() {
    let keys = times(&[0]);
    let sticky = policy(Tolerance::Exact, Some(None));
    for coverage in [Coverage::Sparse, Coverage::Dense] {
        let selected = select(&keys, coverage, at(1, 100 * MS), &even(), &sticky);
        assert_eq!(selected, picked(0, MatchKind::Retained));
    }
}

#[test]
fn frame_keyed_keys_past_the_timeline_are_rejected() {
    // The frame is further than the timeline reaches, so the key's time is unknown.
    let short = timeline(&[0, 100 * MS, 200 * MS]);
    let sticky = policy(Tolerance::Exact, Some(None));
    let selected = select(
        &frames(&[5]),
        Coverage::Sparse,
        at(7, 700 * MS),
        &short,
        &sticky,
    );
    assert_eq!(selected, Selection::None);
    // A key inside the timeline is still kept.
    let selected = select(
        &frames(&[1]),
        Coverage::Sparse,
        at(2, 200 * MS),
        &short,
        &sticky,
    );
    assert_eq!(selected, picked(0, MatchKind::Retained));
}

#[test]
fn frame_keyed_ignores_a_frame_time_before_the_key_time() {
    // A frame whose timestamp disagrees with the timeline must not retain a "future" sample.
    let sticky = policy(Tolerance::Exact, Some(None));
    let selected = select(
        &frames(&[1]),
        Coverage::Sparse,
        at(2, 50 * MS),
        &even(),
        &sticky,
    );
    assert_eq!(selected, Selection::None);
}
