//! What a viewer is shown while it is paused and when it resumes.

use camera::Status;
use foundation::MediaTime;

use super::{Update, delivering, frame, rig, update};
use crate::live_sources::fake::{content, doc, rtsp, unit};

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

    // The viewer resumes: it is shown the next unit released, not one released while it was paused.
    paused.pause(false);
    video.units.send(unit(1, Some(5_000), 22)).await.unwrap();
    docs.send(doc(1, "5000 events")).await.unwrap();
    docs.send(doc(1, "6000")).await.unwrap();
    assert_eq!(
        frame(&mut paused).await.frame.id().timestamp,
        MediaTime(5_000)
    );
}
