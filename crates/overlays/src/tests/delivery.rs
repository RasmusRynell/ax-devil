//! Which maps and samples are sent, in which order, and when again.

use std::{fs, time::Duration};

use foundation::CancelToken;
use playback::Rank;

use super::{Env, cells, quiet, sample_ids, settle, shown, timeline, until_ready, want};
use crate::{
    LaneStatus, OverlayEvent, ViewerOverlays,
    state::{READY_PER_SOURCE, State},
};

/// A viewer of one MOT file with a box in each of `frames`, ready, with lane 0 subscribed.
async fn shown_viewer(env: &Env, frames: u64) -> ViewerOverlays {
    let mut viewer = env.open(vec![Some(env.mot("a.txt", 0..frames))], frames);
    until_ready(&mut viewer, &[0]).await;
    viewer.set_policy(0, 1, shown().0, true);
    viewer
}

async fn next_within(viewer: &mut ViewerOverlays, rank: Rank) -> Option<OverlayEvent> {
    tokio::time::timeout(Duration::from_millis(300), viewer.next_event(Some(rank)))
        .await
        .ok()
        .flatten()
}

#[tokio::test]
async fn an_unsubscribed_lane_gets_nothing() {
    let env = Env::new();
    let mut viewer = env.open(vec![Some(env.mot("a.txt", 0..10))], 10);
    until_ready(&mut viewer, &[0]).await;
    viewer.want(want(5, 2, 2));
    assert!(quiet(&mut viewer).await);

    viewer.set_policy(0, 1, shown().0, true);
    assert!(!settle(&mut viewer).await.maps.is_empty());
    viewer.set_policy(0, 2, shown().0, false);
    assert!(quiet(&mut viewer).await, "hiding stops the lane");
}

#[tokio::test]
async fn runs_cover_the_data_window_once_inside_chunks_and_samples_go_nearest_first() {
    let env = Env::new();
    let mut viewer = shown_viewer(&env, 600).await;
    viewer.want(want(300, 100, 400));

    let sent = settle(&mut viewer).await;

    // The window is 200..=599 (clamped to the video).
    let found = cells(&sent, 0);
    let frames: Vec<u64> = found.iter().map(|&(frame, _)| frame).collect();
    assert_eq!(frames, (200..600).collect::<Vec<_>>(), "each frame once");
    assert!(
        found
            .iter()
            .all(|&(frame, sample)| u64::from(sample) == frame)
    );
    for run in &sent.maps {
        let last = run.first + run.samples.len() as u64 - 1;
        assert!(run.samples.len() <= 256 && run.first / 256 == last / 256);
        assert_eq!((run.policy_revision, run.data_revision), (1, 1));
        assert_eq!(run.kinds.len(), run.samples.len());
    }
    assert_eq!(
        (sent.maps[0].first, sent.maps[0].samples.len()),
        (256, 256),
        "the run of the playhead's chunk goes first"
    );
    assert_eq!(sent.order[0], "map", "maps go before samples at equal rank");
    // The playhead, then ahead, nearest first, then behind.
    let expected: Vec<u64> = std::iter::once(300)
        .chain(301..600)
        .chain((200..300).rev())
        .collect();
    assert_eq!(sample_ids(&sent), expected);
}

#[tokio::test]
async fn data_goes_only_up_to_the_rank_of_the_waiting_frame_and_wins_ties() {
    let env = Env::new();
    let mut viewer = shown_viewer(&env, 20).await;
    viewer.want(want(10, 2, 2));

    // A frame at the playhead waits: the playhead's map and sample tie with it and go first.
    let Some(OverlayEvent::Map(run)) = next_within(&mut viewer, (0, 0)).await else {
        panic!("the map ties with the frame");
    };
    assert_eq!((run.first, run.samples.len(), run.frame), (8, 5, 10));
    let Some(OverlayEvent::Sample(item)) = next_within(&mut viewer, (0, 0)).await else {
        panic!("the sample ties with the frame");
    };
    assert_eq!((item.sample, item.frame), (10, 10));
    assert!(next_within(&mut viewer, (0, 0)).await.is_none());

    // One frame ahead is next; further data waits for the frames before it.
    let Some(OverlayEvent::Sample(item)) = next_within(&mut viewer, (1, 1)).await else {
        panic!("the next sample ties with the frame one ahead");
    };
    assert_eq!(item.sample, 11);
    assert!(next_within(&mut viewer, (1, 1)).await.is_none());
}

#[tokio::test]
async fn a_sample_that_leaves_the_window_and_returns_is_sent_again_with_its_map() {
    let env = Env::new();
    let mut viewer = shown_viewer(&env, 20).await;
    viewer.want(want(10, 2, 2));
    let first = settle(&mut viewer).await;
    assert_eq!(sample_ids(&first), [10, 11, 12, 9, 8]);

    // One step on: only what enters the window is new.
    viewer.want(want(11, 2, 2));
    let stepped = settle(&mut viewer).await;
    assert_eq!(sample_ids(&stepped), [13]);
    assert_eq!(cells(&stepped, 0), [(13, 13)]);

    // Away and back.
    viewer.want(want(19, 2, 0));
    assert_eq!(sample_ids(&settle(&mut viewer).await), [19, 18, 17]);
    viewer.want(want(10, 2, 2));
    let back = settle(&mut viewer).await;
    assert_eq!(sample_ids(&back), [10, 11, 12, 9, 8]);
    assert_eq!(cells(&back, 0).len(), 5, "the maps are sent again too");
}

#[tokio::test]
async fn lanes_on_one_file_share_a_source_and_a_policy_change_resends_its_maps_and_samples() {
    let env = Env::new();
    let overlay = env.mot("a.txt", 0..10);
    let mut viewer = env.open(vec![Some(overlay.clone()), Some(overlay)], 10);
    assert_eq!(viewer.lane_sources(), [Some(0), Some(0)]);
    until_ready(&mut viewer, &[0, 1]).await;
    viewer.want(want(4, 1, 2));
    viewer.set_policy(0, 1, shown().0, true);
    viewer.set_policy(1, 1, shown().0, true);

    let sent = settle(&mut viewer).await;

    assert_eq!(
        sample_ids(&sent),
        [4, 5, 6, 3],
        "one source, each sample once"
    );
    assert_eq!(cells(&sent, 0), cells(&sent, 1));
    assert_eq!(cells(&sent, 0).len(), 4);

    // The viewer keeps only the samples the maps it holds name, and holds no map of the new revision yet: the
    // samples go again with the maps, under the new revision.
    viewer.set_policy(1, 2, shown().0, true);
    let again = settle(&mut viewer).await;
    assert_eq!(sample_ids(&again), [4, 5, 6, 3]);
    assert_eq!(cells(&again, 1), cells(&sent, 1));
    assert!(
        again
            .maps
            .iter()
            .all(|run| run.lane == 1 && run.policy_revision == 2)
    );

    // Hiding one lane sends the samples the other still needs again, for the window it has now.
    viewer.set_policy(1, 3, shown().0, false);
    viewer.want(want(5, 1, 2));
    assert_eq!(sample_ids(&settle(&mut viewer).await), [5, 6, 7, 4]);
}

#[tokio::test]
async fn data_the_consumer_did_not_deliver_comes_again_while_the_window_holds_it() {
    let env = Env::new();
    let mut viewer = shown_viewer(&env, 20).await;
    viewer.want(want(10, 2, 2));
    let sent = settle(&mut viewer).await;
    assert_eq!(sample_ids(&sent), [10, 11, 12, 9, 8]);

    // The writer dropped the map run and the sample of the playhead: the run's frames and the samples it names come
    // again, and the sample dropped alone with them.
    viewer.undelivered(&OverlayEvent::Map(sent.maps[0].clone()));
    viewer.undelivered(&OverlayEvent::Sample(sent.samples[0].clone()));
    let again = settle(&mut viewer).await;
    assert_eq!(cells(&again, 0), cells(&sent, 0));
    assert_eq!(sample_ids(&again), [10, 11, 12, 9, 8]);

    // A sample the window no longer needs does not come back.
    viewer.want(want(19, 0, 0));
    settle(&mut viewer).await;
    viewer.undelivered(&OverlayEvent::Sample(sent.samples[0].clone()));
    assert!(quiet(&mut viewer).await);
}

#[tokio::test]
async fn an_unreadable_sample_goes_as_an_empty_item_with_a_problem_for_each_lane() {
    let env = Env::caching();
    let overlay = env.mot("a.txt", 0..5);
    drop(
        env.tracks
            .registry
            .open(&overlay, super::SIZE, &CancelToken::new())
            .expect("opens"),
    );
    let artifact = fs::read_dir(env.path("cache/scenes"))
        .expect("artifacts")
        .next()
        .expect("one artifact")
        .expect("entry")
        .path();
    let mut bytes = fs::read(&artifact).expect("read");
    bytes[10] ^= 0xFF;
    fs::write(&artifact, bytes).expect("damage");
    let mut viewer = env.open(vec![Some(overlay.clone()), Some(overlay)], 5);
    until_ready(&mut viewer, &[0, 1]).await;
    viewer.want(want(0, 0, 4));
    viewer.set_policy(0, 1, shown().0, true);
    viewer.set_policy(1, 1, shown().0, true);

    let sent = settle(&mut viewer).await;

    assert_eq!(
        sent.samples.len(),
        5,
        "no lane waits for the damaged sample"
    );
    let size = |sample: u64| {
        sent.samples
            .iter()
            .find(|item| item.sample == sample)
            .map(|item| item.item.len())
    };
    assert!(size(0) < size(1), "sample 0 is the empty item");
    let lanes: Vec<u16> = sent.problems.iter().map(|(lane, _)| *lane).collect();
    assert_eq!(lanes, [0, 1]);
    assert!(
        sent.problems[0].1.contains("damaged"),
        "{:?}",
        sent.problems
    );
}

#[tokio::test]
async fn encoded_items_wait_in_a_bounded_number_for_a_slow_consumer() {
    let env = Env::new();
    let viewer = {
        let viewer = shown_viewer(&env, 600).await;
        viewer.want(want(300, 100, 100));
        viewer
    };
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(viewer.waiting_items(0), READY_PER_SOURCE);
}

/// Everything the state would send now.
fn drain(state: &mut State) -> Vec<OverlayEvent> {
    std::iter::from_fn(|| state.pull(None)).collect()
}

#[tokio::test]
async fn a_new_load_takes_a_new_revision_and_starts_every_set_from_empty() {
    let env = Env::new();
    let overlay = env.mot("a.txt", 0..10);
    let track = env
        .tracks
        .registry
        .open(&overlay, super::SIZE, &CancelToken::new())
        .expect("opens");
    let timeline = timeline(10);
    let item = track.item(4).expect("item");
    let mut state = State::new(vec![Some(overlay)], 9);
    state.finish_load(0, Ok((track.clone(), 100)), &timeline);
    state.set_want(want(4, 1, 1), &timeline);
    state.set_policy(0, 1, shown().0, true, &timeline);
    state.finish_encode(0, 1, 4, item.clone(), None);
    let first = drain(&mut state);
    assert!(matches!(
        first[..],
        [
            OverlayEvent::Status { .. },
            OverlayEvent::Status {
                status: LaneStatus::Ready(_),
                ..
            },
            OverlayEvent::Map(_),
            OverlayEvent::Sample(_)
        ]
    ));

    state.finish_load(0, Ok((track, 100)), &timeline);

    let second = drain(&mut state);
    let [
        OverlayEvent::Status {
            status: LaneStatus::Ready(ready),
            ..
        },
        OverlayEvent::Map(run),
    ] = &second[..]
    else {
        panic!("a new status and the maps again, no sample: {second:?}");
    };
    assert_eq!(
        (ready.data_revision, run.data_revision, run.policy_revision),
        (2, 2, 1)
    );
    assert_eq!(
        state.next_encode(0),
        Some((4, 2)),
        "the sample is encoded again"
    );
    state.finish_encode(0, 1, 4, item, None);
    assert!(
        drain(&mut state).is_empty(),
        "an item of the old revision is dropped"
    );
}
