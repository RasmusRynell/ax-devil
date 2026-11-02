//! What the viewer holds, against a model of the viewer that applies the events in order: the backend alone decides
//! what is held (`Evict`), which map cells are kept (`Window`) and stays within the byte budget.

use super::{
    Env, shown,
    viewer_model::{Rig, backward, evicted, exact, samples, sticky},
    want,
};
use crate::{OverlayEvent, state::READY_PER_SOURCE};

#[test]
fn a_sticky_sample_that_stays_selected_across_a_jump_is_neither_evicted_nor_sent_again() {
    let env = Env::new();
    let mut rig = Rig::sparse(&env, [0], 21, 1);
    rig.policy(0, 1, sticky(), true);
    rig.want(want(1, 1, 1));
    rig.pump();
    rig.check();
    assert_eq!(rig.sent(), [0]);

    rig.want(want(10, 1, 1));
    let jumped = rig.pump();

    assert!(
        samples(&jumped).is_empty() && evicted(&jumped).is_empty(),
        "{jumped:?}"
    );
    assert!(matches!(
        jumped[0],
        OverlayEvent::Window { first: 9, last: 11 }
    ));
    assert_eq!(
        rig.model.cells.get(&(0, 10)),
        Some(&0),
        "the new map names the sample the viewer holds"
    );
    rig.check();
}

#[test]
fn a_more_urgent_sample_evicts_the_least_urgent_to_make_room_before_it_goes() {
    let env = Env::new();
    let mut rig = Rig::new(&env, 0..10, 10, 1);
    rig.budget(2);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(1, 1, 2));
    rig.pump();
    rig.check();
    assert_eq!(
        rig.sent(),
        [1, 2],
        "sample 0 and 3 wait: both sent samples outrank them"
    );

    // Sample 3 is the playhead and outranks 1 and 2, which now rank by their distance behind it.
    rig.want(want(3, 2, 0));
    let moved = rig.pump();

    let evict = moved
        .iter()
        .position(|event| matches!(event, OverlayEvent::Evict { samples, .. } if samples == &[1]))
        .expect("the least urgent sample is evicted");
    let sample = moved
        .iter()
        .position(|event| matches!(event, OverlayEvent::Sample(item) if item.sample == 3))
        .expect("sample 3 is sent");
    assert!(
        evict < sample,
        "the eviction precedes the sample that needs the room"
    );
    assert_eq!(rig.sent(), [2, 3]);
    rig.check();
}

#[test]
fn a_narrower_window_is_told_before_the_maps_selected_for_it() {
    let env = Env::new();
    let mut rig = Rig::new(&env, 0..30, 30, 1);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(10, 3, 3));
    rig.pump();
    rig.check();

    rig.want(want(10, 1, 1));
    let narrowed = rig.pump();
    assert!(matches!(
        narrowed[0],
        OverlayEvent::Window { first: 9, last: 11 }
    ));
    assert!(
        rig.model
            .cells
            .keys()
            .all(|&(_, frame)| (9..=11).contains(&frame))
    );
    rig.check();

    rig.want(want(10, 3, 3));
    let widened = rig.pump();
    let window = widened
        .iter()
        .position(|event| matches!(event, OverlayEvent::Window { first: 7, last: 13 }))
        .expect("the window is told");
    let first_map = widened
        .iter()
        .position(|event| matches!(event, OverlayEvent::Map(_)))
        .expect("the maps of the new frames");
    assert!(window < first_map);
    rig.check();
}

#[test]
fn a_sample_handed_back_is_sent_again_and_a_stale_hand_back_does_not_undo_the_resend() {
    let env = Env::new();
    let mut rig = Rig::new(&env, 0..10, 10, 1);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(5, 0, 0));
    let first = rig.pump();
    let Some(OverlayEvent::Sample(old)) =
        first.iter().find(|e| matches!(e, OverlayEvent::Sample(_)))
    else {
        panic!("a sample is sent");
    };

    rig.state.undelivered(&OverlayEvent::Sample(old.clone()));
    let again = rig.pump();
    let [OverlayEvent::Sample(new)] = &again[..] else {
        panic!("the sample is sent again: {again:?}");
    };
    assert_ne!(new.serial, old.serial);

    rig.state.undelivered(&OverlayEvent::Sample(old.clone()));
    assert!(
        rig.pump().is_empty(),
        "the older send does not un-send the newer"
    );
    rig.check();
}

#[test]
fn a_policy_change_sends_maps_and_missing_samples_and_flipping_back_sends_nothing() {
    let env = Env::new();
    // Samples at frames 0 and 5: exact selects 1 at frame 5 only, sticky also 0 for the frames before it.
    let mut rig = Rig::sparse(&env, [0, 5], 10, 1);
    rig.policy(0, 1, exact(), true);
    rig.want(want(5, 2, 2));
    rig.pump();
    assert_eq!(rig.sent(), [1]);

    rig.policy(0, 2, sticky(), true);
    let sticky_sent = rig.pump();
    assert_eq!(
        samples(&sticky_sent),
        [0],
        "the sample the viewer holds is not sent again"
    );
    assert!(
        sticky_sent
            .iter()
            .any(|event| matches!(event, OverlayEvent::Map(_)))
    );
    rig.check();

    rig.policy(0, 3, exact(), true);
    let exact_sent = rig.pump();
    assert_eq!(
        evicted(&exact_sent),
        [0],
        "the sample only the old policy needed is evicted"
    );
    rig.check();

    // Flipped back before the eviction goes out: the viewer still holds the sample.
    rig.policy(0, 4, sticky(), true);
    rig.pump();
    rig.policy(0, 5, exact(), true);
    rig.policy(0, 6, sticky(), true);
    let flipped = rig.pump();
    assert!(
        samples(&flipped).is_empty() && evicted(&flipped).is_empty(),
        "{flipped:?}"
    );
    rig.check();
}

#[test]
fn a_new_revision_discards_the_evictions_still_waiting_and_clears_what_was_sent() {
    let env = Env::new();
    let mut rig = Rig::new(&env, 0..20, 20, 1);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(10, 2, 2));
    rig.pump();
    rig.want(want(15, 2, 2));
    assert!(!rig.state.sources[0].evicting.is_empty());

    rig.state
        .finish_load(0, Ok((rig.track.clone(), 100)), &rig.timeline);

    let pulled = rig.pump();
    assert!(evicted(&pulled).is_empty(), "{pulled:?}");
    assert!(
        pulled
            .iter()
            .all(|event| !matches!(event, OverlayEvent::Sample(item) if item.data_revision == 1))
    );
    rig.check();
}

#[test]
fn the_most_urgent_sample_is_encoded_when_the_ready_items_are_full_of_worse_ones() {
    let env = Env::new();
    let mut rig = Rig::new(&env, 0..20, 20, 1);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(10, 2, 6));
    while let Some((sample, revision)) = rig.state.next_encode(0) {
        let item = rig.track.item(sample as usize).expect("item");
        rig.state.finish_encode(0, revision, sample, item, None);
    }
    assert_eq!(rig.state.sources[0].ready.len(), 4, "the consumer is slow");

    rig.want(want(14, 6, 2));

    assert_eq!(rig.state.next_encode(0), Some((14, 1)));
    assert_eq!(rig.state.sources[0].ready.len(), 3, "the worst makes room");
}

/// Samples of 1 box, then `blockers` of 20 that do not fit beside the first, then two of 1 that do.
fn blocked_then_small(blockers: usize) -> Rig {
    let env = Env::new();
    let counts: Vec<u32> = [1]
        .into_iter()
        .chain(std::iter::repeat_n(20, blockers))
        .chain([1, 1])
        .collect();
    let mut rig = Rig::boxes(&env, &counts, 1);
    let small = rig.sizes[0];
    assert!(rig.sizes[1] > 2 * small, "the blockers are large");
    rig.budget_bytes(3 * small);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(0, 0, counts.len() as u32 - 1));
    rig.pump();
    rig
}

#[test]
fn a_sample_that_does_not_fit_does_not_hold_back_the_smaller_ones_after_it() {
    let rig = blocked_then_small(1);
    assert_eq!(rig.sent(), [0, 2, 3]);
    rig.check();
}

#[test]
fn smaller_samples_are_reached_past_more_blocked_ones_than_the_ready_items_hold() {
    let rig = blocked_then_small(READY_PER_SOURCE + 1);
    assert_eq!(rig.sent(), [0, 6, 7]);
    rig.check();
}

#[test]
fn samples_of_mixed_sizes_are_sent_by_rank_and_a_larger_one_evicts_only_worse_ones() {
    let env = Env::new();
    // Small, large, small, large, small.
    let mut rig = Rig::boxes(&env, &[1, 20, 1, 20, 1], 1);
    let (small, large) = (rig.sizes[0], rig.sizes[1]);
    rig.budget_bytes(large + 2 * small);
    rig.policy(0, 1, shown().0.expect("policy"), true);

    rig.want(want(1, 1, 2));
    rig.pump();
    assert_eq!(rig.sent(), [0, 1, 2], "the large sample 3 waits, 0 fits");
    rig.check();

    // Sample 3 is the playhead and outranks everything sent: the worst go until it fits, and 0 comes back.
    rig.want(want(3, 3, 0));
    let moved = rig.pump();
    assert_eq!(evicted(&moved), [0, 1]);
    assert_eq!(rig.sent(), [0, 2, 3]);
    rig.check();
}

#[test]
fn a_sample_larger_than_the_budget_goes_alone_when_nothing_better_is_sent() {
    let env = Env::new();
    let mut rig = Rig::boxes(&env, &[1, 1, 20, 1, 1], 1);
    rig.budget_bytes(2 * rig.sizes[0]);
    assert!(rig.sizes[2] > rig.state.budget);
    rig.policy(0, 1, shown().0.expect("policy"), true);
    rig.want(want(0, 0, 1));
    rig.pump();
    assert_eq!(rig.sent(), [0, 1]);

    // Sample 1 is sent and outranks the large one, which waits while sample 3 goes.
    rig.want(want(1, 0, 2));
    rig.pump();
    assert_eq!(rig.sent(), [1, 3]);
    rig.check();

    // At the playhead the large one outranks what is sent and takes the budget alone.
    rig.want(want(2, 0, 2));
    rig.pump();
    assert_eq!(rig.sent(), [2]);
    rig.check();
}

/// A small deterministic generator, so a failure replays.
struct Walk(u64);

impl Walk {
    fn next(&mut self, below: u64) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0 % below
    }
}

#[test]
fn a_random_walk_of_wants_policies_reloads_and_late_hand_backs_keeps_the_viewer_and_backend_in_agreement()
 {
    let env = Env::new();
    let mut rig = Rig::sparse(&env, (0..60).filter(|frame| frame % 3 != 1), 60, 2);
    rig.budget(3);
    rig.drop_every = 7;
    let mut walk = Walk(0x9E37_79B9_7F4A_7C15);
    let mut revisions = [1_u32; 2];
    for lane in 0..2 {
        rig.policy(lane, 1, sticky(), true);
    }
    rig.want(want(30, 3, 3));
    for _ in 0..200 {
        match walk.next(10) {
            0 => {
                let lane = walk.next(2) as usize;
                revisions[lane] += 1;
                let matching = if walk.next(2) == 0 { sticky() } else { exact() };
                rig.policy(lane as u16, revisions[lane], matching, walk.next(4) != 0);
            }
            1 if walk.next(4) == 0 => {
                rig.state
                    .finish_load(0, Ok((rig.track.clone(), 100)), &rig.timeline);
            }
            _ => {
                let moved = if walk.next(2) == 0 { want } else { backward };
                rig.want(moved(
                    walk.next(60),
                    walk.next(6) as u32,
                    walk.next(8) as u32,
                ));
            }
        }
        // What the writer dropped on the previous step reaches the overlays only now.
        rig.hand_back();
        rig.pump();
        rig.check();
    }
    rig.hand_back();
    rig.drop_every = 0;
    rig.pump();
    rig.check();
    assert!(rig.dropped.is_empty());
}
