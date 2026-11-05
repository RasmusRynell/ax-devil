//! Checks against a real Axis camera: `cargo test -p camera --test real_camera -- --ignored`. Every test is ignored, and skips itself unless
//! `AX_DEVIL_TARGET_ADDR` is set (with `AX_DEVIL_TARGET_USER` and `AX_DEVIL_TARGET_PASS`; the MQTT round trip also
//! needs `AX_DEVIL_MQTT_BROKER_ADDR`, `_USER` and `_PASS`, and `_PORT` when it is not 1883). Never part of CI.
// A skipped test says why on stderr; there is no logger in a test.
#![allow(clippy::print_stderr)]

use std::env;
use std::time::Duration;

use camera::{
    Broker, Camera, Device, Feed, Head, Host, Login, Reconnect, Scheme, Secret, Status,
    StreamRequest, Web, open_mqtt, open_stream,
};
use tokio::time::{Instant, timeout, timeout_at};

fn var(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.is_empty())
}

fn login(user: &str, password: &str) -> Option<Login> {
    Some(Login {
        user: var(user)?,
        password: Secret::new(var(password).unwrap_or_default()),
    })
}

/// The camera from `AX_DEVIL_TARGET_*`, or `None` (the test then skips).
fn target() -> Option<Camera> {
    let Some(addr) = var("AX_DEVIL_TARGET_ADDR") else {
        eprintln!("skipped: AX_DEVIL_TARGET_ADDR is not set");
        return None;
    };
    let mut camera = Camera::new(Host::parse(&addr).expect("AX_DEVIL_TARGET_ADDR is a host"));
    if var("AX_DEVIL_TARGET_PROTOCOL").is_some_and(|p| p.eq_ignore_ascii_case("http")) {
        camera.web = Web {
            scheme: Scheme::Http,
            port: Scheme::Http.default_port(),
        };
    }
    camera.login = login("AX_DEVIL_TARGET_USER", "AX_DEVIL_TARGET_PASS");
    Some(camera)
}

fn broker() -> Option<Broker> {
    let Some(addr) = var("AX_DEVIL_MQTT_BROKER_ADDR") else {
        eprintln!("skipped: AX_DEVIL_MQTT_BROKER_ADDR is not set");
        return None;
    };
    let mut broker = Broker::new(Host::parse(&addr).expect("AX_DEVIL_MQTT_BROKER_ADDR is a host"));
    if let Some(port) = var("AX_DEVIL_MQTT_BROKER_PORT") {
        broker.port = port.parse().expect("AX_DEVIL_MQTT_BROKER_PORT is a port");
    }
    broker.login = login("AX_DEVIL_MQTT_BROKER_USER", "AX_DEVIL_MQTT_BROKER_PASS");
    Some(broker)
}

fn stream(video: bool, metadata: bool) -> StreamRequest {
    StreamRequest {
        head: Head::FIRST,
        video: video.then_some(None),
        metadata,
        url: None,
        reconnect: Reconnect::RTSP,
    }
}

#[tokio::test]
#[ignore = "needs a real camera"]
async fn the_camera_identifies_itself_and_accepts_the_login() {
    let Some(camera) = target() else { return };
    let device = Device::new(&camera).expect("device");
    let identity = device.identify().await.expect("identify");
    assert!(!identity.serial.is_empty());
    assert_eq!(
        device.sign_in().await.expect("sign in").serial,
        identity.serial
    );
}

#[tokio::test]
#[ignore = "needs a real camera"]
async fn a_snapshot_is_a_jpeg_and_resolutions_are_listed() {
    let Some(camera) = target() else { return };
    let device = Device::new(&camera).expect("device");
    // `snapshot` checks the JPEG signature itself.
    assert!(
        !device
            .snapshot(Head::FIRST)
            .await
            .expect("snapshot")
            .is_empty()
    );
    assert!(!device.image_sizes().await.expect("sizes").heads.is_empty());
}

#[tokio::test]
#[ignore = "needs a real camera"]
async fn three_seconds_of_video_has_a_keyframe_and_capture_times_that_do_not_go_back() {
    let Some(camera) = target() else { return };
    let mut live = open_stream(&camera, stream(true, false));
    let (mut keyframes, mut last, mut frames) = (0, None, 0);
    let end = Instant::now() + Duration::from_secs(3);
    while let Ok(Some(unit)) = timeout_at(end, live.video.recv()).await {
        frames += 1;
        keyframes += usize::from(unit.keyframe);
        if let Some(capture) = unit.capture {
            assert!(
                last.is_none_or(|last| capture >= last),
                "{capture:?} after {last:?}"
            );
            last = Some(capture);
        }
    }
    if let Status::Failed(error) = &*live.status.borrow() {
        panic!("the stream failed: {error}");
    }
    assert!(frames > 0, "no video");
    assert!(keyframes > 0, "no keyframe in {frames} frames");
    assert!(last.is_some(), "no capture times");
    live.close().await;
}

#[tokio::test]
#[ignore = "needs a real camera"]
async fn onvif_metadata_arrives_within_ten_seconds() {
    let Some(camera) = target() else { return };
    let mut live = open_stream(&camera, stream(false, true));
    let feed = timeout(Duration::from_secs(10), live.metadata.recv())
        .await
        .expect("no metadata in 10 s");
    assert!(matches!(feed, Some(Feed::Item(_))), "{feed:?}");
    live.close().await;
}

#[tokio::test]
#[ignore = "needs a real camera and an MQTT broker"]
async fn mqtt_analytics_arrive_and_the_camera_is_left_clean() {
    let (Some(camera), Some(broker)) = (target(), broker()) else {
        return;
    };
    let device = Device::new(&camera).expect("device");
    let before = device.analytics_publishers().await.expect("publishers");
    let key = device
        .analytics_sources()
        .await
        .expect("sources")
        .into_iter()
        .next()
        .expect("a data source");
    let mut live = open_mqtt(&camera, &broker, key, Reconnect::MQTT);
    let received = timeout(Duration::from_secs(20), async {
        loop {
            match live.messages.recv().await {
                Some(Feed::Item(message)) => return Some(message),
                Some(Feed::Gap { .. }) => {}
                None => return None,
            }
        }
    })
    .await;
    let status = live.status.borrow().clone();
    live.close().await;
    assert_eq!(
        device.analytics_publishers().await.expect("publishers"),
        before,
        "publishers left behind"
    );
    let message = received
        .expect("no message in 20 s")
        .unwrap_or_else(|| panic!("no message; {status:?}"));
    assert!(!message.payload.is_empty());
}
