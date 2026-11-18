//! What waits for the decoder when messages arrive faster than it reads.

use std::{
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Duration, SystemTime},
};

use bytes::Bytes;
use camera::{Feed as Delivery, Message, Status};
use foundation::{CancelToken, Interrupt};
use handler_api::{DecodeError, DecodeSummary, Decoder, Input, Record, SceneSink, Spec, Values};
use overlays::NONE_CELL;
use scene_store::Feed;
use tokio::time::timeout;
use workspace::LiveOverlay;

use super::{frame, luma, rig};
use crate::{
    bridge::{Doc, Waiting, sessions},
    fake::{content, unit},
    overlay::{Arrival, Effects, Intake, QUEUE},
};

fn arrival(n: u8) -> Arrival {
    Arrival::Doc {
        epoch: 1,
        doc: Doc {
            bytes: Bytes::from(vec![n]),
            capture: None,
            topic: Some("topic".to_owned()),
            stamp: None,
        },
        time_us: Some(i64::from(n) * 1000),
    }
}

/// Opens the first input, reads document 1, pushes 2 and 3 meanwhile, and reads whatever the decoder gets next.
fn reads_after_a_burst(waiting: Waiting) -> Vec<Vec<u8>> {
    let (offers, mut feed) = sessions();
    let mut intake = Intake::new(offers, QUEUE, waiting);
    let cancel = CancelToken::new();
    let mut input = feed.next_input(&cancel).expect("the first session");
    let mut read = |count: usize| {
        (0..count)
            .map(|_| input.next(&cancel).unwrap().unwrap().bytes.to_vec())
            .collect::<Vec<_>>()
    };

    assert_eq!(intake.take(arrival(1), None), Effects::default());
    assert_eq!(read(1), [[1]]);
    // A replaced message is no loss of the lane: no gap, nothing behind.
    assert_eq!(intake.take(arrival(2), None), Effects::default());
    assert_eq!(intake.take(arrival(3), None), Effects::default());
    let expected = if waiting == Waiting::Newest { 1 } else { 2 };
    read(expected)
}

#[test]
fn mqtt_decodes_only_the_newest_waiting_message_and_datahub_every_one() {
    assert_eq!(reads_after_a_burst(Waiting::Newest), [[3]]);
    assert_eq!(reads_after_a_burst(Waiting::Queue), [[2], [3]]);
}

fn message(payload: String) -> Delivery<Message> {
    Delivery::Item(Message {
        epoch: 1,
        topic: "scene".to_owned(),
        payload: Bytes::from(payload),
        stamp: None,
        received: SystemTime::now(),
    })
}

/// An ADF v1 frame with one detection, `second` seconds after the epoch.
fn adf(second: u32) -> Delivery<Message> {
    message(format!(
        r#"{{"frame": {{"timestamp": "1970-01-01T00:00:{second:02}Z", "detections": [{{"object_track_id": 1, "bounding_box": {{"left": 0.1, "top": 0.1, "right": 0.2, "bottom": 0.2}}}}], "track_events": []}}}}"#
    ))
}

/// A built-in decoder under another id that counts how often the decoder asks for a record. The decoder asks again only
/// when it has finished the record before, so a count says how many records it has taken in.
struct Counting {
    inner: Arc<dyn Decoder>,
    spec: Spec,
    asked: Arc<AtomicUsize>,
}

struct CountingInput<'a> {
    inner: &'a mut dyn Input,
    asked: &'a AtomicUsize,
}

impl Input for CountingInput<'_> {
    fn path(&self) -> Option<&Path> {
        self.inner.path()
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        self.asked.fetch_add(1, Ordering::SeqCst);
        self.inner.next(cancel)
    }
}

impl Decoder for Counting {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn reads_live(&self) -> bool {
        self.inner.reads_live()
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let mut counting = CountingInput {
            inner: input,
            asked: &self.asked,
        };
        self.inner.decode(&mut counting, options, sink, cancel)
    }
}

/// Waits, for at most 20 s, until the decoder has asked for a record `count` times.
async fn asked(asked: &AtomicUsize, count: usize) {
    timeout(Duration::from_secs(20), async {
        while asked.load(Ordering::SeqCst) < count {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .expect("the decoder took the record in");
}

#[tokio::test]
async fn a_malformed_mqtt_message_costs_only_itself_in_a_built_in_decoder() {
    let mut rig = rig();
    let built_in = rig.handlers.decoder("ADF_V1_FRAME").cloned().unwrap();
    let counted = Arc::new(AtomicUsize::new(0));
    let spec = Spec {
        id: "ADF_COUNTED".into(),
        ..built_in.spec().clone()
    };
    let counting = Counting {
        inner: built_in,
        spec,
        asked: Arc::clone(&counted),
    };
    rig.handlers = rig
        .handlers
        .clone()
        .with_decoders([Arc::new(counting) as _]);
    let overlay = LiveOverlay::Mqtt {
        handler: "ADF_COUNTED".into(),
        broker_host: "broker".into(),
        broker_port: 1883,
        broker_user: String::new(),
        broker_pass: workspace::Secret::new(""),
        source_key: "scene".into(),
    };
    let mut viewer = rig.open(&content("cam", overlay)).await;
    let (video, wire) = (rig.fake.videos()[0].clone(), rig.fake.messages()[0].clone());
    video.status.send(Status::Live { epoch: 1 }).unwrap();
    wire.status.send(Status::Live { epoch: 1 }).unwrap();

    // Only the newest waiting message is kept, so each is sent once the decoder has finished the one before: its
    // next request for a record says so.
    asked(&counted, 1).await;
    for (sent, message) in [adf(1), message("{not json".into()), adf(2), adf(3)]
        .into_iter()
        .enumerate()
    {
        wire.messages.send(message).await.unwrap();
        asked(&counted, sent + 2).await;
    }

    video
        .units
        .send(unit(1, Some(2_000_000), 40))
        .await
        .unwrap();
    let shown = frame(&mut viewer).await;
    assert!(
        !viewer.lane_ready().unwrap().warnings.is_empty(),
        "the malformed message was read and skipped"
    );
    assert_eq!(luma(&shown), 40);
    assert_ne!(
        shown.out.unwrap().map.samples,
        [NONE_CELL],
        "the message after the malformed one is drawn"
    );
}
