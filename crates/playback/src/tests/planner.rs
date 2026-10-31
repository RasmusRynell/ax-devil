use crate::Direction::{Backward, Forward};
use crate::plan::plan;
use crate::plan::reads_on;
use crate::testing::{spec, want};
use crate::window::prefetch_windows;
use crate::{FrameSet, Span};

fn spans(planned: &[Span]) -> Vec<(u64, std::ops::RangeInclusive<u64>)> {
    planned
        .iter()
        .map(|span| (span.keyframe, span.keep.clone()))
        .collect()
}

#[test]
fn spans_cover_only_the_missing_window_frames_nearest_in_the_direction_of_travel_first() {
    let video = spec(100, 10);
    // Budget 20, playing 85/15: window 22..=41 around playhead 25.
    let playing = want(25, 17, 3, Forward);
    let fresh = plan(&video, &[playing], 20, &FrameSet::new(), &[]);
    assert_eq!(spans(&fresh), [(20, 22..=29), (30, 30..=39), (40, 40..=41)]);

    let held = FrameSet::from(22..=33);
    let resumed = plan(&video, &[playing], 20, &held, &[]);
    assert_eq!(spans(&resumed), [(30, 34..=39), (40, 40..=41)]);

    // Paused 25/75: the frames behind are fetched after the ones ahead, though the group behind is nearer.
    let behind = want(25, 5, 15, Forward);
    let order: Vec<u64> = plan(&video, &[behind], 20, &FrameSet::new(), &[])
        .iter()
        .map(|span| span.keyframe)
        .collect();
    assert_eq!(order, [20, 30, 10]);
}

#[test]
fn backward_travel_fetches_whole_groups_behind_first() {
    let video = spec(100, 10);
    let stepping = want(25, 3, 16, Backward);
    let planned = plan(&video, &[stepping], 20, &FrameSet::new(), &[]);
    assert_eq!(
        spans(&planned),
        [(20, 20..=28), (10, 10..=19)],
        "the group behind is decoded whole, before the playhead reaches it"
    );
}

#[test]
fn a_group_longer_than_the_budget_keeps_only_a_segment() {
    let video = spec(300, 100);
    let planned = plan(
        &video,
        &[want(150, 9, 1, Forward)],
        10,
        &FrameSet::new(),
        &[],
    );
    assert_eq!(spans(&planned), [(100, 149..=158)]);
}

#[test]
fn walking_back_through_a_group_longer_than_the_budget_decodes_a_block_per_half_budget() {
    let video = spec(300, 100);
    let mut starts = Vec::new();
    for playhead in (230..=250).rev() {
        let window = prefetch_windows(&video, &[want(playhead, 2, 9, Backward)], 12).remove(0);
        assert!(window.contains(&playhead));
        assert!(window.end() - window.start() < 12, "within the budget");
        if starts.last() != Some(window.start()) {
            starts.push(*window.start());
        }
    }
    assert!(
        starts.len() <= 21 / 6 + 2,
        "a new block about every half budget, not every step: {starts:?}"
    );
}

#[test]
fn wants_share_the_budget_and_in_flight_spans_win_ties() {
    let video = spec(100, 10);
    let wants = [want(25, 10, 10, Forward), want(75, 10, 10, Forward)];
    let windows = prefetch_windows(&video, &wants, 24);
    assert_eq!(
        windows,
        [20..=31, 70..=81],
        "each want gets half of the budget"
    );

    let idle = plan(&video, &wants, 24, &FrameSet::new(), &[]);
    assert_eq!(idle[0].keyframe, 20, "equal urgency: lower keyframe first");
    let running = Span {
        keyframe: 70,
        keep: 70..=79,
    };
    let busy = plan(&video, &wants, 24, &FrameSet::new(), &[running]);
    assert_eq!(
        busy[0].keyframe, 70,
        "the span a worker is already on stays first"
    );
}

#[test]
fn a_decoder_reads_on_unless_that_would_decode_a_whole_extra_group() {
    let video = spec(100, 10);
    let keys = &video.keyframes;
    let cases = [
        (Some(11), 15, true),
        (Some(11), 25, true),
        (Some(10), 25, false),
        (Some(11), 45, false),
        (Some(20), 25, true),
        (Some(16), 15, false),
        (Some(100), 15, false),
        (None, 5, false),
    ];
    for (position, first, expected) in cases {
        assert_eq!(
            reads_on(keys, position, first),
            expected,
            "{position:?} to {first}"
        );
    }
}

#[test]
fn decoder_threads_share_the_cores_between_all_decoders_of_all_open_sources() {
    use crate::decoder::decode_threads;
    // (cores, open sources, threads per decoder)
    let cases = [
        (16, 1, 8),
        (16, 2, 4),
        (16, 4, 2),
        (16, 20, 1),
        (4, 1, 2),
        (4, 3, 1),
        (2, 1, 1),
        (64, 1, 8),
    ];
    for (cores, sources, expected) in cases {
        assert_eq!(
            decode_threads(cores, sources),
            expected,
            "{cores} cores, {sources} sources"
        );
    }
    assert_eq!(
        decode_threads(8, 0),
        decode_threads(8, 1),
        "no sources counts as one"
    );
}
