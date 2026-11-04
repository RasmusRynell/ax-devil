//! The live socket, through a fake feed: what a follower and a holder are told, and what each may change.

use super::*;
use crate::live_sources::{
    LiveSources,
    fake::{FakeConnector, Hold, TestDecoder, content, doc, matching, rtsp, unit},
};
use api_types::playback::{KIND_FRAME_MAP, KIND_SAMPLE, KIND_VIDEO_FRAME};
use camera::Status;
use handlers::Handlers;
use playback::{Authority, ClockIntent};
use std::{sync::Arc, time::Duration};
use tokio::sync::mpsc;

fn follower() -> Authority {
    Authority::Follow {
        intent: ClockIntent {
            frame: 0,
            playing: false,
            speed_centi: 100,
            at_us: 0,
        },
    }
}

fn client(message: &ClientMessage) -> Result<Message, axum::Error> {
    Ok(Message::text(serde_json::to_string(message).unwrap()))
}

/// What the viewer was sent: the name of a text message, or the kind of a binary envelope.
fn kind(message: &Message) -> String {
    match message {
        Message::Text(body) => {
            let sent: ServerMessage = serde_json::from_str(body).unwrap();
            format!("{sent:?}")
                .split(['(', ' '])
                .next()
                .unwrap()
                .to_owned()
        }
        Message::Binary(bytes) => format!("envelope {}", bytes[5]),
        other => panic!("unexpected {other:?}"),
    }
}

#[tokio::test]
async fn a_follower_is_told_the_stream_then_each_frame_with_its_overlay_and_its_actions_are_ignored()
 {
    let (fake, hold) = (FakeConnector::default(), Arc::new(Hold::default()));
    let handlers = Handlers::built_in().with_decoders([Arc::new(TestDecoder(hold)) as _]);
    let sources = LiveSources::new(Arc::new(fake.clone()));
    let content = content("cam", rtsp());
    let viewer = sources.open(&content, &handlers, matching()).await.unwrap();

    let (_holder, authority) = watch::channel(follower());
    let gate = Gate::new(authority, || 0);
    let (outbox, mut sent) = mpsc::channel::<Outgoing>(1);
    let (to_server, from_client) = mpsc::unbounded_channel();
    let mut incoming = Box::pin(futures_util::stream::unfold(
        from_client,
        |mut from| async move { Some((from.recv().await?, from)) },
    ));
    let (_stop, mut closing) = watch::channel(false);
    let library = Library::built_in();
    let serving = stream(
        viewer,
        Setup {
            label: "Live",
            matching: matching(),
            library: &library,
            audience: Audience::HOST,
        },
        &mut incoming,
        &outbox,
        &mut closing,
        gate,
    );
    tokio::pin!(serving);

    let video = fake.videos()[0].clone();
    let docs = video.docs.clone().unwrap();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    to_server
        .send(client(&ClientMessage::LanePolicy {
            lane: 0,
            revision: 1,
            policy: policy_wire(&matching()),
            subscribed: true,
        }))
        .unwrap();
    // A follower only watches: this changes nothing.
    to_server
        .send(client(&ClientMessage::Live {
            action: api_types::live::LiveAction::Pause,
        }))
        .unwrap();
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    docs.send(doc(1, "1000")).await.unwrap();
    docs.send(doc(1, "2000")).await.unwrap();

    let mut order = Vec::new();
    let mut asked = false;
    let mut inspected = false;
    while !inspected {
        tokio::select! {
            ended = &mut serving => panic!("the stream ended: {:?}", ended.err().map(|_| ())),
            message = tokio::time::timeout(Duration::from_secs(20), sent.recv()) => {
                let message = message.expect("a message in time").expect("the outbox is open").message;
                order.push(kind(&message));
                if kind(&message) == format!("envelope {KIND_SAMPLE}") && !asked {
                    asked = true;
                    to_server.send(client(&ClientMessage::Inspect {
                        source: 0,
                        data_revision: 0,
                        sample: 0,
                        entity: "1".into(),
                    })).unwrap();
                }
                if let Message::Text(body) = &message
                    && let Ok(ServerMessage::Inspected { inspection, .. }) = serde_json::from_str(body)
                {
                    assert!(
                        matches!(inspection, api_types::inspect::InspectionWire::Found { .. }),
                        "{inspection:?}"
                    );
                    inspected = true;
                }
            }
        }
    }
    let prefix: Vec<_> = order.iter().take(3).map(String::as_str).collect();
    assert_eq!(prefix, ["LiveOpened", "LaneStatus", "LiveStatus"]);
    let binary: Vec<String> = order
        .iter()
        .filter(|kind| kind.starts_with("envelope"))
        .cloned()
        .collect();
    assert_eq!(
        binary,
        [KIND_SAMPLE, KIND_VIDEO_FRAME, KIND_FRAME_MAP].map(|kind| format!("envelope {kind}"))
    );
}

#[tokio::test]
async fn a_holder_paused_on_its_own_is_shown_the_stream_again_when_it_begins_to_follow() {
    let (fake, hold) = (FakeConnector::default(), Arc::new(Hold::default()));
    let handlers = Handlers::built_in().with_decoders([Arc::new(TestDecoder(hold)) as _]);
    let sources = LiveSources::new(Arc::new(fake.clone()));
    let content = content("cam", rtsp());
    let viewer = sources.open(&content, &handlers, matching()).await.unwrap();

    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    let (outbox, mut sent) = mpsc::channel::<Outgoing>(1);
    let (to_server, from_client) = mpsc::unbounded_channel();
    let mut incoming = Box::pin(futures_util::stream::unfold(
        from_client,
        |mut from| async move { Some((from.recv().await?, from)) },
    ));
    let (_stop, mut closing) = watch::channel(false);
    let library = Library::built_in();
    let serving = stream(
        viewer,
        Setup {
            label: "Live",
            matching: matching(),
            library: &library,
            audience: Audience::HOST,
        },
        &mut incoming,
        &outbox,
        &mut closing,
        gate,
    );
    tokio::pin!(serving);

    let video = fake.videos()[0].clone();
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    to_server
        .send(client(&ClientMessage::Live {
            action: api_types::live::LiveAction::Pause,
        }))
        .unwrap();
    // The pause is applied before anything else happens.
    let mut told = 0;
    while told < 3 {
        tokio::select! {
            ended = &mut serving => panic!("the stream ended: {:?}", ended.err().map(|_| ())),
            message = sent.recv() => {
                message.expect("the outbox is open");
                told += 1;
            }
        }
    }
    let idle = tokio::time::timeout(Duration::from_millis(200), &mut serving).await;
    assert!(idle.is_err(), "the stream is still serving");

    holder.send(follower()).unwrap();
    video.units.send(unit(1, Some(1_000), 20)).await.unwrap();
    loop {
        tokio::select! {
            ended = &mut serving => panic!("the stream ended: {:?}", ended.err().map(|_| ())),
            message = tokio::time::timeout(Duration::from_secs(20), sent.recv()) => {
                let message = message.expect("a frame in time").expect("the outbox is open").message;
                if kind(&message) == format!("envelope {KIND_VIDEO_FRAME}") {
                    break;
                }
            }
        }
    }
}

#[tokio::test]
async fn a_viewer_that_asks_without_reading_is_cut_off_instead_of_growing_the_backlog() {
    let (fake, hold) = (FakeConnector::default(), Arc::new(Hold::default()));
    let handlers = Handlers::built_in().with_decoders([Arc::new(TestDecoder(hold)) as _]);
    let sources = LiveSources::new(Arc::new(fake));
    let content = content("cam", rtsp());
    let viewer = sources.open(&content, &handlers, matching()).await.unwrap();

    let (_holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    // Nothing reads the outbox, so the viewer is never sent more than its first message.
    let (outbox, _unread) = mpsc::channel::<Outgoing>(1);
    let (to_server, from_client) = mpsc::unbounded_channel();
    let mut incoming = Box::pin(futures_util::stream::unfold(
        from_client,
        |mut from| async move { Some((from.recv().await?, from)) },
    ));
    let (_stop, mut closing) = watch::channel(false);
    let library = Library::built_in();
    for request in 0..=MAX_NOTICES as u32 {
        to_server
            .send(client(&ClientMessage::Appearances {
                lane: 0,
                request,
                entity: "1".into(),
            }))
            .unwrap();
    }
    let ended = stream(
        viewer,
        Setup {
            label: "Live",
            matching: matching(),
            library: &library,
            audience: Audience::HOST,
        },
        &mut incoming,
        &outbox,
        &mut closing,
        gate,
    );
    let ended = tokio::time::timeout(Duration::from_secs(20), ended)
        .await
        .expect("the session ended");
    assert!(matches!(ended, Err(End::Failed(_))));
}
