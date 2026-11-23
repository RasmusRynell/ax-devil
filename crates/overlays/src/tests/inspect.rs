//! Which samples a viewer's inspection may read: only those it was sent and still holds.

use super::{Env, next, settle, shown, until_ready, want};
use crate::OverlayEvent;

#[tokio::test]
async fn only_a_sample_sent_under_the_current_revision_and_still_held_can_be_inspected() {
    let env = Env::new();
    let mut viewer = env.open(vec![Some(env.mot("a.txt", 0..30))], 30);
    until_ready(&mut viewer, &[0]).await;
    viewer.set_policy(0, 1, shown().0, true);
    viewer.want(want(2, 1, 1));
    assert!(matches!(
        next(&mut viewer).await,
        OverlayEvent::Window { first: 1, last: 3 }
    ));
    assert!(
        viewer.inspect_target(0, 1, 2).is_none(),
        "the sample is not sent yet"
    );

    let sent = settle(&mut viewer).await;

    let mut held: Vec<u64> = sent.samples.iter().map(|item| item.sample).collect();
    held.sort_unstable();
    assert_eq!(held, [1, 2, 3]);
    for item in &sent.samples {
        let (track, _, frame) = viewer
            .inspect_target(0, 1, item.sample)
            .expect("a held sample");
        assert_eq!((frame, track.snapshot().keys.len()), (item.frame, 30));
    }
    assert!(viewer.inspect_target(0, 2, 2).is_none(), "another revision");
    assert!(viewer.inspect_target(1, 1, 2).is_none(), "another source");
    assert!(viewer.inspect_target(0, 1, 4).is_none(), "never sent");

    viewer.want(want(20, 1, 1));
    let moved = settle(&mut viewer).await;
    let mut evicted: Vec<u32> = moved.evicts.iter().flat_map(|(_, s)| s.clone()).collect();
    evicted.sort_unstable();
    assert_eq!(evicted, [1, 2, 3]);
    for gone in 1..=3 {
        assert!(viewer.inspect_target(0, 1, gone).is_none(), "{gone}");
    }
    assert!(viewer.inspect_target(0, 1, 20).is_some());
}
