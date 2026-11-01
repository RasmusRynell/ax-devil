//! Which frames of the window are prepared, in which order, and when again.

use super::{Env, overlays, places, quiet, until_ready, want};

#[tokio::test]
async fn frames_go_once_per_residency_nearest_the_playhead_first() {
    let env = Env::new();
    let mut viewer = env.open(vec![Some(env.mot("a.txt", 0..20))], 20);
    until_ready(&mut viewer, &[0]).await;

    viewer.want(want(10, 2, 2));
    let (found, _) = overlays(&mut viewer, 5).await;
    // The playhead, then ahead (direction of travel) nearest first, then behind.
    assert_eq!(places(&found), [(0, 10), (0, 11), (0, 12), (0, 9), (0, 8)]);
    assert!(quiet(&mut viewer).await, "nothing is sent twice");

    // One step on: only the frame entering the window is new.
    viewer.want(want(11, 2, 2));
    let (found, _) = overlays(&mut viewer, 1).await;
    assert_eq!(places(&found), [(0, 13)]);
    assert!(quiet(&mut viewer).await);

    // Away and back: what left the window is forgotten and sent again.
    viewer.want(want(19, 2, 0));
    let (found, _) = overlays(&mut viewer, 3).await;
    assert_eq!(places(&found), [(0, 19), (0, 18), (0, 17)]);
    viewer.want(want(10, 2, 2));
    let (found, _) = overlays(&mut viewer, 5).await;
    assert_eq!(places(&found), [(0, 10), (0, 11), (0, 12), (0, 9), (0, 8)]);
    assert!(quiet(&mut viewer).await);
}

#[tokio::test]
async fn lanes_share_each_frame_before_the_next_and_hidden_lanes_send_none() {
    let env = Env::new();
    let lanes = vec![Some(env.mot("a.txt", 0..10)), Some(env.mot("b.txt", 0..10))];
    let mut viewer = env.open(lanes, 10);
    let mut hidden = env.service.lane_defaults();
    hidden.visible = false;
    viewer.set_view(1, 1, hidden.clone());
    until_ready(&mut viewer, &[0, 1]).await;

    viewer.want(want(4, 0, 1));
    let (found, _) = overlays(&mut viewer, 2).await;
    assert_eq!(places(&found), [(0, 4), (0, 5)]);
    assert!(quiet(&mut viewer).await, "the hidden lane sends nothing");

    // Shown again with a new revision: its window follows.
    hidden.visible = true;
    viewer.set_view(1, 2, hidden);
    let (found, _) = overlays(&mut viewer, 2).await;
    assert_eq!(places(&found), [(1, 4), (1, 5)]);
    assert!(found.iter().all(|overlay| overlay.revision == 2));
}
