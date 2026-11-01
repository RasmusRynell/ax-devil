//! Lane statuses: loading, ready, failed, and closing.

use std::time::Duration;

use foundation::{CancelToken, Interrupt};
use scene::TimeKind;

use super::{Env, SIZE, next, overlay_at, settle, shown, want};
use crate::{LaneStatus, OverlayEvent};

#[tokio::test]
async fn a_lane_is_loading_then_ready_with_the_file_filter_warnings_and_item_size() {
    let env = Env::new();
    // The second row is damaged: the decoder skips it and says so.
    let overlay = env.overlay(
        "gt.txt",
        "MOT_FILE",
        "1,1,100,100,50,80,0.9,-1,-1,-1\nnot a row\n3,1,100,100,50,80,0.9,-1,-1,-1\n",
    );
    let mut viewer = env.open(vec![Some(overlay), None], 5);
    assert_eq!(viewer.lane_sources(), [Some(0), None]);

    assert_eq!(
        next(&mut viewer).await,
        OverlayEvent::Status {
            lane: 0,
            status: LaneStatus::Loading
        }
    );
    let OverlayEvent::Status {
        lane: 0,
        status: LaneStatus::Ready(ready),
    } = next(&mut viewer).await
    else {
        panic!("the lane becomes ready");
    };
    assert_eq!(ready.clock, TimeKind::Frame);
    assert_eq!(ready.samples, 2);
    assert_eq!(ready.data_revision, 1);
    assert!(ready.mean_sample_bytes > 0, "the mean item size");
    assert!(!ready.filter.options.is_empty(), "the file's class filters");
    assert!(!ready.warnings.is_empty(), "the skipped row is reported");

    // The lane without an overlay never gets a status or data.
    viewer.want(want(0, 0, 4));
    viewer.set_policy(0, 1, shown().0, true);
    viewer.set_policy(1, 1, shown().0, true);
    let sent = settle(&mut viewer).await;
    assert!(sent.maps.iter().all(|run| run.lane == 0));
}

#[tokio::test]
async fn a_lane_that_cannot_load_fails_with_the_decoders_error_and_the_others_go_on() {
    let env = Env::new();
    let missing = overlay_at("missing", "MOT_FILE", "/does/not/exist.txt".into());
    let Err(Interrupt::Failed(expected)) =
        env.tracks
            .registry
            .open(&missing, SIZE, &CancelToken::new())
    else {
        panic!("the file cannot be opened");
    };
    let good = env.mot("good.txt", 0..5);
    let mut viewer = env.open(vec![Some(missing), Some(good)], 5);
    viewer.want(want(0, 0, 1));
    viewer.set_policy(0, 1, shown().0, true);
    viewer.set_policy(1, 1, shown().0, true);

    let mut failed = None;
    let mut samples = 0;
    while failed.is_none() || samples < 2 {
        match next(&mut viewer).await {
            OverlayEvent::Status {
                lane: 0,
                status: LaneStatus::Failed(error),
            } => failed = Some(error),
            OverlayEvent::Sample(item) => {
                assert_eq!(item.source, 1, "a failed source sends nothing");
                samples += 1;
            }
            OverlayEvent::Map(run) => assert_eq!(run.lane, 1, "a failed lane has no map"),
            _ => {}
        }
    }
    assert_eq!(failed, Some(expected));
}

#[tokio::test]
async fn closing_cancels_a_load_in_progress() {
    let env = Env::holding("slow.txt");
    let slow = env.mot("slow.txt", 0..3);
    let mut viewer = env.open(vec![Some(slow)], 3);
    assert_eq!(
        next(&mut viewer).await,
        OverlayEvent::Status {
            lane: 0,
            status: LaneStatus::Loading
        }
    );

    viewer.close();

    env.cancelled
        .recv_timeout(Duration::from_secs(10))
        .expect("the load saw its cancellation");
}

#[tokio::test]
async fn a_source_thread_that_dies_fails_its_lane_and_then_the_viewer_ends() {
    let env = Env::new();
    let panicking = env.mot("panic.txt", 0..3);
    let mut viewer = env.open(vec![Some(panicking), None], 3);
    assert_eq!(
        next(&mut viewer).await,
        OverlayEvent::Status {
            lane: 0,
            status: LaneStatus::Loading
        }
    );

    let OverlayEvent::Status {
        lane: 0,
        status: LaneStatus::Failed(error),
    } = next(&mut viewer).await
    else {
        panic!("the lane fails instead of staying loading");
    };
    assert_eq!(error.reason, "The overlays stopped unexpectedly");
    assert_eq!(
        viewer.next_event(None).await,
        None,
        "no status for the plain lane"
    );
}
