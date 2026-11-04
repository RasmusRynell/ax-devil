//! What Retry does to a feed's connections.

use std::{sync::atomic::Ordering, time::Duration};

use api_types::live::LiveFeedStatus;
use camera::Status;
use tokio::time::timeout;
use workspace::LiveOverlay;

use super::{Update, rig, update};
use crate::live_sources::fake::{content, damaged};

#[tokio::test(start_paused = true)]
async fn retry_reopens_a_stalled_video_and_leaves_a_live_one_alone() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    rig.fake.videos()[0]
        .status
        .send(Status::Live { epoch: 1 })
        .unwrap();
    update(&mut viewer).await;

    viewer.retry();
    tokio::time::sleep(Duration::from_millis(1)).await;
    assert_eq!(rig.fake.videos().len(), 1, "a live video is not reopened");

    let Update::Status(stalled) = update(&mut viewer).await else {
        panic!("a status");
    };
    assert_eq!(stalled.video, LiveFeedStatus::Stalled { seconds: 5 });
    viewer.retry();
    tokio::time::sleep(Duration::from_millis(1)).await;
    assert_eq!(rig.fake.videos().len(), 2, "a stalled video is reopened");
}

#[tokio::test(start_paused = true)]
async fn a_retry_does_not_reconnect_when_the_last_viewer_leaves_while_it_closes_the_old_connections()
 {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    update(&mut viewer).await;
    let Update::Status(stalled) = update(&mut viewer).await else {
        panic!("a status");
    };
    assert_eq!(stalled.video, LiveFeedStatus::Stalled { seconds: 5 });

    // The retry is in the middle of closing the old connection when the window closes.
    rig.fake.hold_closing(true);
    viewer.retry();
    while !video.closing.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    drop(viewer);
    rig.fake.hold_closing(false);
    rig.sources.shutdown(Duration::from_secs(3)).await;
    assert_eq!(rig.fake.videos().len(), 1, "no connection for nobody");
    assert!(video.closed.load(Ordering::Acquire));
}

#[tokio::test(start_paused = true)]
async fn retries_asked_while_the_old_connections_close_wait_in_one_task() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    update(&mut viewer).await;
    update(&mut viewer).await;

    rig.fake.hold_closing(true);
    viewer.retry();
    while !video.closing.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    let tasks = || {
        tokio::runtime::Handle::current()
            .metrics()
            .num_alive_tasks()
    };
    let one_retry = tasks();
    for _ in 0..10 {
        viewer.retry();
    }
    tokio::time::sleep(Duration::from_millis(1)).await;
    assert_eq!(tasks(), one_retry);

    rig.fake.hold_closing(false);
    while rig.fake.videos().len() != 2 {
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
}

#[tokio::test(start_paused = true)]
async fn a_video_that_delivers_only_pictures_it_cannot_decode_stalls_and_offers_a_retry() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();

    // Units arrive every second, so the connection is not silent, yet no picture is ever shown.
    let mut every = tokio::time::interval(Duration::from_secs(1));
    let mut captured = 0;
    let stalled = timeout(Duration::from_secs(30), async {
        loop {
            tokio::select! {
                _ = every.tick() => {
                    captured += 1_000;
                    video.units.send(damaged(1, captured)).await.unwrap();
                }
                update = viewer.next() => {
                    if let Some(Update::Status(wire)) = update
                        && matches!(wire.video, LiveFeedStatus::Stalled { .. })
                    {
                        return;
                    }
                }
            }
        }
    })
    .await;
    assert!(stalled.is_ok(), "the video was shown as live");

    viewer.retry();
    tokio::time::sleep(Duration::from_millis(1)).await;
    assert_eq!(rig.fake.videos().len(), 2, "the stalled video is reopened");
}
