//! How long a live frame waits for its overlay.

use std::time::Duration;

use camera::Status;
use tokio::time::timeout;

use super::{frame, luma, rig};
use crate::live_sources::fake::{content, rtsp, unit};

#[tokio::test(start_paused = true)]
async fn a_frame_waits_the_whole_deadline_for_an_overlay_that_never_delivers() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    video.units.send(unit(1, Some(1_000), 5)).await.unwrap();

    let early = timeout(Duration::from_millis(1_499), frame(&mut viewer)).await;
    assert!(early.is_err(), "the frame left early");
    let shown = timeout(Duration::from_millis(5), frame(&mut viewer))
        .await
        .expect("the frame leaves at the deadline");
    assert_eq!(luma(&shown), 5);
}
