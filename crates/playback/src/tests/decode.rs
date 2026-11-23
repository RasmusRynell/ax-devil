use crate::Direction::{Backward, Forward};
use crate::FrameSet;
use crate::testing::{Fixture, want};
use crate::worker::Step;
use std::sync::Arc;

fn sorted_unique(ids: &[u64]) -> bool {
    let mut copy = ids.to_vec();
    copy.sort_unstable();
    copy.windows(2).all(|pair| pair[0] != pair[1])
}

#[test]
fn the_window_fills_ahead_of_the_playhead_wherever_the_decoder_is_and_nothing_else_is_kept() {
    for earlier in [100, 5] {
        let mut fx = Fixture::new(120, 10, 20);
        let session = fx.session();
        session.want(want(earlier, 16, 3, Forward));
        fx.drain();

        session.want(want(45, 16, 3, Forward));
        fx.drain();

        assert_eq!(
            fx.cached(),
            FrameSet::from(42..=61),
            "decoder left at {earlier}"
        );
    }
}

#[test]
fn a_decoder_seeks_only_to_a_later_group_and_otherwise_reads_on() {
    // One frame of cache: every want is just its playhead, so each decode shows the seek decision.
    let mut fx = Fixture::new(120, 10, 1);
    let session = fx.session();
    let log = Arc::clone(&fx.log);
    let mut jump_to = |playhead: u64| {
        session.want(want(playhead, 1, 0, Forward));
        fx.drain();
    };

    jump_to(10);
    assert_eq!(log.seeks(), [10]);
    jump_to(15);
    assert_eq!(log.seeks(), [10], "inside the same group");
    jump_to(25);
    assert_eq!(log.seeks(), [10], "the next group is read on to");
    assert_eq!(log.decoded(), (10..=25).collect::<Vec<_>>());
    jump_to(45);
    assert_eq!(log.seeks(), [10, 40], "a later group is sought");
}

#[test]
fn each_group_is_decoded_once_per_pass_under_a_small_budget() {
    let mut fx = Fixture::new(120, 10, 24);
    let session = fx.session();
    for playhead in 0..120 {
        session.want(want(playhead, 19, 4, Forward));
        fx.drain();
    }
    assert!(
        sorted_unique(&fx.log.decoded()),
        "forward pass decoded a frame twice"
    );
    assert!(sorted_unique(&fx.log.seeks()));

    let mut fx = Fixture::new(120, 10, 24);
    let session = fx.session();
    for playhead in (0..120).rev() {
        session.want(want(playhead, 3, 20, Backward));
        fx.drain();
    }
    assert!(
        sorted_unique(&fx.log.decoded()),
        "backward pass decoded a frame twice"
    );
    assert!(sorted_unique(&fx.log.seeks()));
}

#[test]
fn backward_steps_are_cache_hits_and_a_forward_step_turns_the_window_back() {
    let mut fx = Fixture::new(120, 10, 30);
    let session = fx.session();
    session.want(want(95, 4, 25, Backward));
    fx.drain();
    for playhead in (40..95).rev() {
        session.want(want(playhead, 4, 25, Backward));
        assert!(
            fx.cached().contains(playhead),
            "the step to {playhead} was decoded before it was taken"
        );
        fx.drain();
    }

    let decoded = fx.log.decoded().len();
    session.want(want(41, 25, 4, Forward));
    fx.drain();
    assert!(
        fx.log.decoded().len() > decoded,
        "the frames ahead had to be decoded"
    );
    assert!(fx.cached().gaps_in(41..=66).is_empty());
}

#[test]
fn an_abandoned_decoder_keeps_its_place_so_a_later_span_reads_on() {
    let mut fx = Fixture::new(120, 10, 24);
    let session = Arc::new(fx.session());
    let mover = Arc::clone(&session);
    fx.log.on_frame(move |id| {
        if id == 14 {
            mover.want(want(100, 20, 3, Forward));
        }
    });
    session.want(want(0, 20, 3, Forward));

    assert_eq!(fx.step(1), Step::Ran);
    assert_eq!(
        fx.log.decoded(),
        [10, 11, 12, 13, 14],
        "stopped after the frame it was on"
    );
    assert!(
        fx.cached().gaps_in(10..=14).is_empty(),
        "what it decoded stays cached"
    );

    *crate::lock(&fx.log.hook) = None;
    session.want(want(17, 20, 3, Forward));
    fx.drain();
    let decoded = fx.log.decoded();
    assert_eq!(
        fx.log.seeks().iter().filter(|&&key| key == 10).count(),
        1,
        "no second seek"
    );
    assert!(
        (10..20).all(|id| decoded.iter().filter(|&&seen| seen == id).count() == 1),
        "every frame of the group decoded once: {decoded:?}"
    );
}

#[test]
fn a_want_at_a_far_playhead_preempts_at_a_frame_boundary_and_keeps_what_was_decoded() {
    let mut fx = Fixture::new(120, 10, 24);
    let session = Arc::new(fx.session());
    let mover = Arc::clone(&session);
    fx.log.on_frame(move |id| {
        if id == 5 {
            mover.want(want(100, 20, 3, Forward));
        }
    });
    session.want(want(0, 20, 3, Forward));

    assert_eq!(fx.step(0), Step::Ran);
    assert_eq!(fx.log.decoded(), [0, 1, 2, 3, 4, 5]);
    assert!(fx.cached().gaps_in(0..=5).is_empty());

    *crate::lock(&fx.log.hook) = None;
    fx.drain();
    assert!(
        fx.log.decoded().iter().all(|id| !(6..=23).contains(id)),
        "the old span never resumed"
    );
    assert!(fx.cached().gaps_in(100..=119).is_empty());
}

#[test]
fn a_newer_want_inside_the_running_span_does_not_interrupt_it() {
    let mut fx = Fixture::new(120, 10, 24);
    let session = Arc::new(fx.session());
    let mover = Arc::clone(&session);
    fx.log.on_frame(move |id| {
        if id == 5 {
            mover.want(want(8, 20, 3, Forward));
        }
    });
    session.want(want(0, 20, 3, Forward));

    assert_eq!(fx.step(0), Step::Ran);
    assert_eq!(fx.log.decoded(), (0..=9).collect::<Vec<_>>());
    assert!(fx.log.seeks().is_empty());
}

#[test]
fn forward_prefetch_in_a_group_longer_than_the_budget_decodes_to_the_playhead_once() {
    let mut fx = Fixture::new(300, 100, 10);
    let session = fx.session();
    session.want(want(150, 9, 1, Forward));
    fx.drain();
    assert_eq!(fx.cached(), FrameSet::from(149..=158));
    assert_eq!(fx.log.seeks(), [100]);
    assert!(sorted_unique(&fx.log.decoded()));
}

#[test]
fn a_want_that_widens_the_running_span_as_it_ends_fails_none_of_the_new_frames() {
    // A cache of five frames: playhead 2 keeps 2..=6, then the want moves to 4 and the span gains 7..=8 just as
    // its last frame is decoded.
    let mut fx = Fixture::new(120, 10, 5);
    let mut session = Arc::new(fx.session());
    let widen = Arc::clone(&session);
    fx.log.on_frame(move |id| {
        if id == 6 {
            widen.want(want(4, 4, 0, Forward));
        }
    });
    session.want(want(2, 4, 0, Forward));
    assert_eq!(fx.step(0), Step::Ran);
    fx.drain();

    *crate::lock(&fx.log.hook) = None;
    let session = Arc::get_mut(&mut session).unwrap();
    assert_eq!(
        crate::testing::deliveries(session),
        (4..=8).map(|id| (id, false)).collect::<Vec<_>>(),
        "frames beyond the span's old end decode fine and are not reported as failed"
    );
}

#[test]
fn a_decoder_panic_fails_only_the_frames_the_span_had_reached() {
    let mut fx = Fixture::new(120, 10, 5);
    *crate::lock(&fx.log.panic_at) = Some(5);
    let mut session = Arc::new(fx.session());
    let widen = Arc::clone(&session);
    fx.log.on_frame(move |id| {
        if id == 5 {
            widen.want(want(4, 4, 0, Forward));
        }
    });
    session.want(want(2, 4, 0, Forward));
    fx.drain();

    *crate::lock(&fx.log.hook) = None;
    let session = Arc::get_mut(&mut session).unwrap();
    assert_eq!(
        crate::testing::deliveries(session),
        [(4, false), (5, true), (6, true), (7, false), (8, false)],
        "a want that widened the span as the decoder died does not fail frames the decoder never reached"
    );
}
