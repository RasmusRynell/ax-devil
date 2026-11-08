//! What a hover card can show of a stream without connecting to its camera.

use camera::Status;
use futures_util::future::join;
use workspace::LiveOverlay;

use super::{frame, luma, rig};
use crate::{
    glance::Glances,
    live_sources::fake::{content, unit},
};

#[tokio::test]
async fn an_open_stream_offers_its_newest_picture_blank_until_the_first_and_none_once_closed() {
    let rig = rig();
    let content = content("cam", LiveOverlay::None);
    assert!(rig.sources.open_picture(&content).is_none(), "no viewer");

    let mut viewer = rig.open(&content).await;
    assert!(
        matches!(rig.sources.open_picture(&content), Some(None)),
        "connecting"
    );

    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    for (time, level) in [(1_000, 20), (2_000, 21)] {
        video.units.send(unit(1, Some(time), level)).await.unwrap();
        let shown = frame(&mut viewer).await;
        let picture = rig.sources.open_picture(&content).flatten().unwrap();
        assert_eq!((picture.data()[0], luma(&shown)), (level, level));
    }

    drop(viewer);
    assert!(rig.sources.open_picture(&content).is_none(), "closing");
}

#[tokio::test]
async fn a_frame_is_encoded_once_however_many_ask_and_a_newer_one_is_encoded_again() {
    let rig = rig();
    let content = content("cam", LiveOverlay::None);
    let mut viewer = rig.open(&content).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    let glances = Glances::default();
    let mut encoded = Vec::new();
    for (time, level) in [(1_000, 20), (2_000, 21)] {
        video.units.send(unit(1, Some(time), level)).await.unwrap();
        frame(&mut viewer).await;
        let newest = rig.sources.open_picture(&content).flatten().unwrap();
        let (a, b) = join(glances.picture(&newest), glances.picture(&newest)).await;
        let (a, b) = (a.expect("a picture"), b.expect("a picture"));
        assert_eq!(a.as_ptr(), b.as_ptr(), "one encode for both");
        assert_eq!(glances.picture(&newest).await.unwrap().as_ptr(), a.as_ptr());
        encoded.push(a);
    }
    assert_ne!(encoded[0].as_ptr(), encoded[1].as_ptr(), "a newer frame");
}

#[tokio::test]
async fn a_frame_asked_for_while_the_encoder_is_busy_is_encoded_when_asked_for_again() {
    let rig = rig();
    let content = content("cam", LiveOverlay::None);
    let mut viewer = rig.open(&content).await;
    let video = rig.fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    frame(&mut viewer).await;
    // The newest frame stays the newest: nothing replaces it, as on a paused stream.
    let newest = rig.sources.open_picture(&content).flatten().unwrap();
    let glances = Glances::default();

    let busy = glances.hold_encoders();
    assert!(glances.picture(&newest).await.is_none(), "no capacity");
    drop(busy);
    assert!(glances.picture(&newest).await.is_some(), "capacity again");
}
