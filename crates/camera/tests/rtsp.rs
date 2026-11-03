//! RTSP streams against an in-process fake camera: what arrives, how failures are typed and retried, and what a slow
//! reader costs.

mod support;

use std::time::Duration;

use camera::{
    AccessUnit, CaptureTime, ErrorKind, Feed, Head, LiveStream, Reconnect, Secret, Status,
    StreamRequest, open_stream,
};
use support::fake_rtsp::{
    Behaviour, FIRST_CAPTURE, FRAME_NANOS, FakeRtsp, Fault, GOP, PPS, SPS, frame_index,
    sample_document,
};
use support::fake_vapix::PASSWORD;
use tokio::time::timeout;

const WAIT: Duration = Duration::from_secs(10);
/// Quick retries, so the tests do not wait for backoff.
const QUICK: Reconnect = Reconnect {
    first: Duration::from_millis(20),
    max: Duration::from_millis(50),
    silence: Some(Duration::from_secs(5)),
};

fn request(video: bool, metadata: bool) -> StreamRequest {
    StreamRequest {
        head: Head::FIRST,
        video: video.then_some(None),
        metadata,
        url: None,
        reconnect: QUICK,
    }
}

/// Waits for a status `matches` and returns it.
async fn status_where(stream: &mut LiveStream, matches: impl Fn(&Status) -> bool) -> Status {
    let wait = stream.status.wait_for(|status| matches(status));
    timeout(WAIT, wait)
        .await
        .expect("the status in time")
        .expect("the stream is alive")
        .clone()
}

async fn next_unit(stream: &mut LiveStream) -> AccessUnit {
    timeout(WAIT, stream.video.recv())
        .await
        .expect("a frame in time")
        .expect("the stream is alive")
}

async fn next_feed<T>(feed: &mut tokio::sync::mpsc::Receiver<Feed<T>>) -> Feed<T> {
    timeout(WAIT, feed.recv())
        .await
        .expect("an item in time")
        .expect("the stream is alive")
}

#[tokio::test]
async fn video_and_metadata_arrive_with_the_cameras_capture_times() {
    let fake = FakeRtsp::start().await;
    let mut stream = open_stream(&fake.camera(), request(true, true));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;

    let mut units = Vec::new();
    for _ in 0..25 {
        units.push(next_unit(&mut stream).await);
    }
    let keyframes: Vec<usize> = (0..units.len()).filter(|&i| units[i].keyframe).collect();
    assert_eq!(keyframes, [0, 10, 20]);
    let sets = [&[0, 0, 0, 1][..], &SPS, &[0, 0, 0, 1], &PPS].concat();
    assert!(
        units[0].data.starts_with(&sets),
        "a keyframe carries its parameter sets"
    );
    assert!(!units[1].data.starts_with(&sets));
    assert_eq!(units[0].codec.codec, camera::VideoCodec::H264);
    assert_eq!(
        units[0].codec.parameter_sets.as_ref(),
        sets,
        "the described parameter sets"
    );
    for (i, unit) in units.iter().enumerate() {
        assert_eq!(unit.epoch, 0);
        assert_eq!(frame_index(&unit.data), u32::try_from(i).unwrap());
        let CaptureTime(nanos) = unit.capture.expect("a capture time");
        let expected = FIRST_CAPTURE + i64::try_from(i).unwrap() * FRAME_NANOS;
        assert!(
            (nanos - expected).abs() <= 1,
            "frame {i}: {nanos} vs {expected}"
        );
    }

    let Feed::Item(doc) = next_feed(&mut stream.metadata).await else {
        panic!("a document");
    };
    assert_eq!(doc.xml.as_ref(), sample_document().as_slice());
    assert!(doc.capture.is_some());

    let describe = fake
        .seen()
        .into_iter()
        .find(|r| r.method == "DESCRIBE")
        .expect("DESCRIBE");
    assert!(
        describe.uri.contains("onvifreplayext=1") && describe.uri.contains("analytics=polygon")
    );
    for request in fake.seen() {
        assert!(
            !request.uri.contains('@') && !request.uri.contains(PASSWORD),
            "{request:?}"
        );
    }
    stream.close().await;
}

#[tokio::test]
async fn close_returns_only_after_the_camera_has_been_told_to_tear_down() {
    let fake = FakeRtsp::start().await;
    let mut stream = open_stream(&fake.camera(), request(true, false));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    let status = stream.status.clone();
    stream.close().await;
    assert!(
        fake.seen().iter().any(|r| r.method == "TEARDOWN"),
        "the TEARDOWN reached the camera before close returned"
    );
    assert_eq!(*status.borrow(), Status::Closed);
}

#[tokio::test]
async fn a_wrong_password_fails_at_once_without_a_retry_and_is_never_shown() {
    let fake = FakeRtsp::start().await;
    let mut camera = fake.camera();
    let login = camera.login.as_mut().expect("a login");
    let wrong = "wrong-pass-9999";
    login.password = Secret::new(wrong);
    let mut stream = open_stream(&camera, request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    let Status::Failed(error) = &status else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::LoginRejected);
    for text in [
        format!("{status:?}"),
        format!("{error}"),
        format!("{error:?}"),
        format!("{stream:?}"),
    ] {
        assert!(!text.contains(wrong) && !text.contains(PASSWORD), "{text}");
    }
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert_eq!(fake.connections(), 1, "no retry");
    assert_eq!(stream.stats().reconnects, 0);

    camera.login = None;
    let mut stream = open_stream(&camera, request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    assert!(matches!(&status, Status::Failed(e) if e.kind == ErrorKind::LoginRequired));
}

#[tokio::test]
async fn a_stream_without_the_wanted_media_fails_for_good() {
    let fake = FakeRtsp::with(Behaviour {
        video: false,
        ..Behaviour::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    assert!(matches!(&status, Status::Failed(e) if e.kind == ErrorKind::Unsupported));
    assert_eq!(fake.connections(), 1);
}

#[tokio::test]
async fn a_dropped_connection_comes_back_as_the_next_epoch() {
    let fake = FakeRtsp::with(Behaviour {
        first_connection: Fault::Drop { after: 30 },
        ..Behaviour::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), request(true, false));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    let reconnecting =
        status_where(&mut stream, |s| matches!(s, Status::Reconnecting { .. })).await;
    let Status::Reconnecting { error, .. } = &reconnecting else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Lost);
    assert!(!format!("{reconnecting:?}").contains(PASSWORD));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 1 }).await;
    let mut epochs = Vec::new();
    while epochs.last() != Some(&1) {
        epochs.push(next_unit(&mut stream).await.epoch);
    }
    assert!(
        epochs.windows(2).all(|w| w[0] <= w[1]),
        "epochs only grow: {epochs:?}"
    );
    assert_eq!(stream.stats().reconnects, 1);
    stream.close().await;
    assert_eq!(fake.connections(), 2);
}

#[tokio::test]
async fn a_silent_camera_is_reconnected() {
    let fake = FakeRtsp::with(Behaviour {
        first_connection: Fault::Silent { after: 20 },
        ..Behaviour::default()
    })
    .await;
    let mut request = request(true, false);
    request.reconnect.silence = Some(Duration::from_millis(300));
    let mut stream = open_stream(&fake.camera(), request);
    let reconnecting =
        status_where(&mut stream, |s| matches!(s, Status::Reconnecting { .. })).await;
    assert!(
        matches!(&reconnecting, Status::Reconnecting { error, .. } if error.kind == ErrorKind::Silent)
    );
    status_where(&mut stream, |s| *s == Status::Live { epoch: 1 }).await;
    while next_unit(&mut stream).await.epoch == 0 {}
    stream.close().await;
}

#[tokio::test]
async fn a_slow_reader_loses_video_up_to_the_next_keyframe_and_is_told_how_many() {
    let fake = FakeRtsp::with(Behaviour {
        burst: 150,
        ..Behaviour::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), request(true, false));
    while stream.stats().video_dropped < 50 {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let mut queued = Vec::new();
    while let Ok(unit) = stream.video.try_recv() {
        queued.push(frame_index(&unit.data));
    }
    assert_eq!(
        queued,
        (0..queued.len() as u32).collect::<Vec<_>>(),
        "what fit is the start, in order"
    );
    let dropped = stream.stats().video_dropped;
    let next = next_unit(&mut stream).await;
    assert!(
        next.keyframe,
        "after a drop, the next frame delivered is a keyframe"
    );
    assert!(frame_index(&next.data).is_multiple_of(GOP));
    assert!(frame_index(&next.data) > queued.len() as u32);
    assert!(stream.stats().video_dropped >= dropped);
}

#[tokio::test]
async fn a_slow_reader_gets_a_gap_instead_of_silently_missing_metadata() {
    let fake = FakeRtsp::with(Behaviour {
        interval: Duration::from_millis(1),
        ..Behaviour::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), request(false, true));
    while stream.stats().feed_lost < 5 {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let mut queued = 0;
    while let Ok(Feed::Item(_)) = stream.metadata.try_recv() {
        queued += 1;
    }
    assert!(queued > 0);
    let lost = stream.stats().feed_lost;
    let Feed::Gap { lost: gap } = next_feed(&mut stream.metadata).await else {
        panic!("a gap comes before the next document");
    };
    assert!(gap >= lost && gap <= stream.stats().feed_lost);
    assert!(matches!(
        next_feed(&mut stream.metadata).await,
        Feed::Item(_)
    ));
}
