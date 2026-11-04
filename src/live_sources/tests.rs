//! A feed over fake transports: what viewers are shown, and what happens to the connections.

use std::{sync::Arc, sync::atomic::Ordering, time::Duration};

use api_types::live::{LiveFeedStatus, LiveReason, LiveStatusWire};
use camera::{CameraError, ErrorKind, Status};
use foundation::MediaTime;
use handlers::Handlers;
use overlays::NONE_CELL;
use tokio::{sync::mpsc, time::timeout};
use workspace::{LiveOverlay, LiveVideoContent};

mod gate;
mod retry;

use super::{
    LiveSources, LiveViewer, Shown, Spec, Update,
    fake::{FakeConnector, Hold, TestDecoder, content, doc, held_back, matching, mqtt, rtsp, unit},
    feed::Feed,
    source::Slot,
};

struct Rig {
    sources: LiveSources,
    fake: FakeConnector,
    hold: Arc<Hold>,
    handlers: Handlers,
}

fn rig() -> Rig {
    let (fake, hold) = (FakeConnector::default(), Arc::new(Hold::default()));
    let handlers = Handlers::built_in().with_decoders([Arc::new(TestDecoder(hold.clone())) as _]);
    Rig {
        sources: LiveSources::new(Arc::new(fake.clone())),
        fake,
        hold,
        handlers,
    }
}

impl Rig {
    async fn open(&self, content: &LiveVideoContent) -> LiveViewer {
        let mut viewer = self
            .sources
            .open(content, &self.handlers, matching())
            .await
            .expect("opens");
        viewer.set_policy(1, matching(), true);
        viewer
    }
}

/// The next update, or a failure when none comes in 20 real seconds.
async fn update(viewer: &mut LiveViewer) -> Update {
    timeout(Duration::from_secs(20), viewer.next())
        .await
        .expect("an update in time")
        .expect("the feed is there")
}

async fn frame(viewer: &mut LiveViewer) -> Shown {
    loop {
        if let Update::Frame(shown) = update(viewer).await {
            return *shown;
        }
    }
}

/// Sends the overlay's `first` document and waits until it has arrived, so later frames wait for the overlay.
async fn delivering<T>(viewer: &mut LiveViewer, overlay: &mpsc::Sender<T>, first: T) {
    overlay.send(first).await.unwrap();
    loop {
        if let Update::Status(status) = update(viewer).await
            && status.overlay == Some(LiveFeedStatus::Live)
            && !status.waiting
        {
            return;
        }
    }
}

fn luma(shown: &Shown) -> u8 {
    shown.frame.data()[0]
}

fn lost() -> Status {
    Status::Reconnecting {
        error: CameraError::fake(ErrorKind::Lost, ""),
        next_in: Duration::from_millis(250),
    }
}

#[tokio::test]
async fn a_frame_is_paired_with_the_overlay_sample_of_its_capture_time() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    let docs = video.docs.clone().expect("metadata was asked for");
    video.status.send(Status::Live { epoch: 1 }).unwrap();

    // The frame at 1 ms waits for the overlay to pass it, and gets the sample at 1 ms with its item.
    delivering(&mut viewer, &docs, doc(1, "1000")).await;
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    docs.send(doc(1, "2000")).await.unwrap();
    let shown = frame(&mut viewer).await;
    let out = shown.out.as_ref().unwrap();
    assert_eq!((luma(&shown), out.map.samples.clone()), (20, vec![0]));
    assert_eq!(out.items.len(), 1);
    assert_eq!(shown.frame.id().timestamp, MediaTime(1_000));
    assert_eq!(out.map.first, shown.frame.id().sequence_id);

    // Only the sample the viewer was sent can be inspected, not another one of the same snapshot.
    let revision = viewer.lane_ready().unwrap().data_revision;
    assert!(viewer.inspect_target(0, revision, 0).is_some());
    assert!(viewer.inspect_target(0, revision, 1).is_none());

    // A frame far from every sample has no cell.
    video.units.send(unit(1, Some(500_000), 21)).await.unwrap();
    docs.send(doc(1, "600000 empty")).await.unwrap();
    docs.send(doc(1, "700000")).await.unwrap();
    let shown = frame(&mut viewer).await;
    assert_eq!(
        (luma(&shown), shown.out.unwrap().map.samples),
        (21, vec![NONE_CELL])
    );
}

#[tokio::test]
async fn a_picture_a_decoder_held_back_is_paired_with_its_own_overlay_sample() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    let docs = video.docs.clone().expect("metadata was asked for");
    video.status.send(Status::Live { epoch: 1 }).unwrap();

    // The picture of the unit at 1 ms comes out with the unit at 5 ms, which has a sample of its own.
    delivering(&mut viewer, &docs, doc(1, "1000")).await;
    video.units.send(held_back(1, 1_000, 20)).await.unwrap();
    video.units.send(held_back(1, 5_000, 21)).await.unwrap();
    for time in ["5000", "9000"] {
        docs.send(doc(1, time)).await.unwrap();
    }
    let shown = frame(&mut viewer).await;
    let out = shown.out.as_ref().unwrap();
    assert_eq!(shown.frame.id().timestamp, MediaTime(1_000));
    assert_eq!((luma(&shown), out.map.samples.clone()), (20, vec![0]));
    assert_eq!(out.map.first, shown.frame.id().sequence_id);
    let revision = viewer.lane_ready().unwrap().data_revision;
    assert!(viewer.inspect_target(0, revision, 0).is_some());
}

#[tokio::test]
async fn a_decoder_that_falls_behind_costs_a_gap_but_never_the_video() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    let docs = video.docs.clone().unwrap();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    video.units.send(unit(1, Some(1_500), 5)).await.unwrap();
    docs.send(doc(1, "1000")).await.unwrap();
    docs.send(doc(1, "2000")).await.unwrap();
    assert_eq!(luma(&frame(&mut viewer).await), 5);

    rig.hold.close();
    for n in 3..=303 {
        docs.send(doc(1, &(n * 1_000).to_string())).await.unwrap();
    }
    loop {
        if matches!(
            update(&mut viewer).await,
            Update::Status(LiveStatusWire { behind: true, .. })
        ) {
            break;
        }
    }
    // The video goes on while the overlay is behind.
    video
        .units
        .send(unit(1, Some(5_000_000), 30))
        .await
        .unwrap();
    assert_eq!(luma(&frame(&mut viewer).await), 30);

    rig.hold.open();
    let (mut caught_up, mut gap) = (false, None);
    while !caught_up || gap.is_none() {
        match update(&mut viewer).await {
            Update::Status(status) => caught_up |= !status.behind,
            Update::Gap { time_us } => gap = Some(time_us),
            Update::Frame(_) | Update::Events(_) => {}
        }
    }
    // The loss began while the video was at its newest frame.
    assert_eq!(gap, Some(1_500));

    // The decoder started over and reads again.
    docs.send(doc(1, "2000000")).await.unwrap();
    docs.send(doc(1, "3000000")).await.unwrap();
    video
        .units
        .send(unit(1, Some(2_000_000), 40))
        .await
        .unwrap();
    let shown = frame(&mut viewer).await;
    assert_eq!(luma(&shown), 40);
    assert_ne!(shown.out.unwrap().map.samples, [NONE_CELL]);
}

#[tokio::test(start_paused = true)]
async fn the_status_follows_the_connection_and_a_video_that_goes_silent_stalls_after_five_seconds()
{
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    let video = rig.fake.videos()[0].clone();
    let video_status = |status: Update| match status {
        Update::Status(wire) => wire.video,
        _ => panic!("a status"),
    };
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    assert_eq!(
        video_status(update(&mut viewer).await),
        LiveFeedStatus::Live
    );

    let from = tokio::time::Instant::now();
    assert_eq!(
        video_status(update(&mut viewer).await),
        LiveFeedStatus::Stalled { seconds: 5 }
    );
    assert_eq!(from.elapsed(), Duration::from_secs(5));

    video.status.send(lost()).unwrap();
    assert_eq!(
        video_status(update(&mut viewer).await),
        LiveFeedStatus::Reconnecting {
            attempt: 1,
            reason: LiveReason::Lost
        }
    );
    video
        .status
        .send(Status::Failed(CameraError::fake(
            ErrorKind::LoginRejected,
            "",
        )))
        .unwrap();
    assert_eq!(
        video_status(update(&mut viewer).await),
        LiveFeedStatus::Failed {
            reason: LiveReason::LoginRejected
        }
    );
}

#[tokio::test(start_paused = true)]
async fn a_video_whose_units_carry_no_capture_time_stalls_instead_of_staying_live() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", LiveOverlay::None)).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    update(&mut viewer).await;
    video.units.send(unit(1, None, 9)).await.unwrap();

    let Update::Status(stalled) = update(&mut viewer).await else {
        panic!("a status");
    };
    assert_eq!(stalled.video, LiveFeedStatus::Stalled { seconds: 5 });
}

#[tokio::test]
async fn events_go_on_arriving_while_a_viewer_is_paused_and_while_every_viewer_is() {
    let rig = rig();
    let mut paused = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    let docs = video.docs.clone().expect("metadata was asked for");
    video.status.send(Status::Live { epoch: 1 }).unwrap();

    // Nobody watches: the video is not decoded, and the event still reaches the Events list.
    paused.pause(true);
    delivering(&mut paused, &docs, doc(1, "1000 events")).await;
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    docs.send(doc(1, "2000")).await.unwrap();
    loop {
        match update(&mut paused).await {
            Update::Events(events) => {
                assert_eq!(events.samples[0].time, MediaTime(1_000));
                break;
            }
            Update::Frame(_) => panic!("a paused viewer was shown a picture"),
            Update::Status(_) | Update::Gap { .. } => {}
        }
    }

    // Another window watches: it is shown the picture with its events, and the paused one still gets only the events.
    let mut watching = rig.open(&content("cam", rtsp())).await;
    video.units.send(unit(1, Some(3_000), 21)).await.unwrap();
    docs.send(doc(1, "3000 events")).await.unwrap();
    docs.send(doc(1, "4000")).await.unwrap();
    let shown = frame(&mut watching).await;
    assert_eq!(
        shown.out.unwrap().events.unwrap().samples[0].time,
        MediaTime(3_000)
    );
    loop {
        match update(&mut paused).await {
            Update::Events(events) => {
                assert_eq!(events.samples[0].time, MediaTime(3_000));
                break;
            }
            Update::Frame(_) => panic!("a paused viewer was shown a picture"),
            Update::Status(_) | Update::Gap { .. } => {}
        }
    }
}

#[tokio::test]
async fn windows_share_a_feed_and_every_connection_is_closed_when_it_ends_or_the_app_quits() {
    let rig = rig();
    let content = content("cam", mqtt("scene"));
    let (first, second) = (rig.open(&content).await, rig.open(&content).await);
    assert_eq!(rig.fake.videos().len(), 1, "one pipeline for two windows");
    let (video, messages) = (rig.fake.videos()[0].clone(), rig.fake.messages()[0].clone());

    drop(first);
    rig.sources.shutdown(Duration::from_secs(3)).await;
    // The shutdown closed what the second window still held, and waited for the camera to be given back.
    assert!(video.closed.load(Ordering::Acquire) && messages.closed.load(Ordering::Acquire));
    drop(second);

    // The last window out ends the feed; the quit waits for that too.
    let rig = self::rig();
    let viewer = rig.open(&content).await;
    let messages = rig.fake.messages()[0].clone();
    drop(viewer);
    rig.sources.shutdown(Duration::from_secs(3)).await;
    assert!(messages.closed.load(Ordering::Acquire));
    assert!(
        rig.sources
            .open(&content, &rig.handlers, matching())
            .await
            .is_err()
    );
}

#[tokio::test]
async fn a_camera_whose_mqtt_client_is_taken_shows_its_overlay_as_failed_busy_and_the_video_plays()
{
    let rig = rig();
    let _first = rig.open(&content("cam", mqtt("scene"))).await;
    let mut second = rig.open(&content("cam", mqtt("other"))).await;
    let video = rig.fake.videos()[1].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    video.units.send(unit(1, Some(1_000), 7)).await.unwrap();

    let mut failed = false;
    let mut shown = false;
    while !failed || !shown {
        match update(&mut second).await {
            Update::Status(status) => {
                failed |= status.overlay
                    == Some(LiveFeedStatus::Failed {
                        reason: LiveReason::Busy,
                    });
            }
            Update::Frame(frame) => shown |= luma(&frame) == 7,
            Update::Gap { .. } | Update::Events(_) => {}
        }
    }
}

#[tokio::test]
async fn another_stream_of_a_camera_waits_for_the_closing_one_to_give_back_its_mqtt_client() {
    let rig = rig();
    let first = rig.open(&content("cam", mqtt("scene"))).await;
    let mut other = content("cam", mqtt("scene"));
    other.camera.resolution = Some("640x480".into());

    // The last window of the first stream has left, and its close has not finished: the second must not start yet.
    drop(first);
    let mut second = rig.open(&other).await;
    assert_eq!(
        rig.fake.messages().len(),
        2,
        "the second connected once the first let go"
    );
    rig.fake.messages()[1]
        .status
        .send(Status::Live { epoch: 1 })
        .unwrap();
    loop {
        if let Update::Status(status) = update(&mut second).await {
            assert_ne!(
                status.overlay,
                Some(LiveFeedStatus::Failed {
                    reason: LiveReason::Busy
                })
            );
            if status.overlay == Some(LiveFeedStatus::Live) {
                break;
            }
        }
    }
}

#[tokio::test]
async fn a_second_closer_waits_until_the_first_has_given_the_camera_back() {
    let rig = rig();
    let spec = Spec::of(&content("cam", mqtt("scene")));
    let decoder = rig.handlers.decoder(TestDecoder::ID).cloned();
    let feed = Feed::start(spec, Arc::new(rig.fake.clone()), decoder).expect("starts");
    let messages = rig.fake.messages()[0].clone();

    // The last window's retirement is under way when the quit asks for the close.
    let retirement = tokio::spawn({
        let feed = Arc::clone(&feed);
        async move { feed.close().await }
    });
    tokio::task::yield_now().await;
    assert!(!messages.closed.load(Ordering::Acquire), "still closing");
    feed.close().await;
    assert!(messages.closed.load(Ordering::Acquire));
    assert!(feed.is_closed());
    retirement.await.expect("finishes");
}

#[tokio::test(start_paused = true)]
async fn a_quit_waits_for_a_feed_whose_last_window_has_just_left() {
    let rig = rig();
    let spec = Spec::of(&content("cam", mqtt("scene")));
    let decoder = rig.handlers.decoder(TestDecoder::ID).cloned();
    let feed = Feed::start(spec.clone(), Arc::new(rig.fake.clone()), decoder).expect("starts");
    let messages = rig.fake.messages()[0].clone();

    // The feed is `Closing` and its camera is still held; the quit starts here, however the close was started.
    let (done, closing) = tokio::sync::watch::channel(false);
    rig.sources
        .0
        .slots
        .lock()
        .unwrap()
        .insert(spec, Slot::Closing(closing));
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(1)).await;
        feed.close().await;
        done.send_replace(true);
    });

    rig.sources.shutdown(Duration::from_secs(3)).await;
    assert!(messages.closed.load(Ordering::Acquire));
}

#[tokio::test]
async fn a_frame_released_at_the_deadline_draws_the_sample_still_open_for_it() {
    let rig = rig();
    let mut viewer = rig.open(&content("cam", rtsp())).await;
    let video = rig.fake.videos()[0].clone();
    let docs = video.docs.clone().expect("metadata was asked for");
    video.status.send(Status::Live { epoch: 1 }).unwrap();

    // No later timestamp comes, so the sample at 1 ms stays open and the frame waits out the deadline.
    delivering(&mut viewer, &docs, doc(1, "1000")).await;
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    let shown = frame(&mut viewer).await;
    let out = shown.out.as_ref().unwrap();
    assert_eq!((luma(&shown), out.map.samples.clone()), (20, vec![0]));
    assert_eq!(out.items.len(), 1);
}
