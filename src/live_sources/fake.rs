//! Doubles for the tests: a connector that serves what a test pushes into it, a codec that needs no FFmpeg, and an
//! overlay handler whose decoder a test can hold up.

use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::{
        Arc, Condvar, LazyLock, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use bytes::Bytes;
use camera::{
    AccessUnit, CameraError, CaptureTime, CodecParams, ErrorKind, Feed, Message, MetadataDoc,
    Status, VideoCodec,
};
use foundation::{
    CancelToken, ColorDesc, ErrorCode, FrameFailure, FrameId, FrameLayout, Interrupt, Matrix,
    MediaTime, PixelFormat, Plane, UserError, VideoFrame,
};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Input, OnEqualKey, SceneSink, Spec, Values,
};
use overlay_sync::{MatchBasis as Basis, MatchPolicy, Sticky};
use scene::{
    BoundingBox, Coverage, Entity, EntityId, Event, FilterPolicy, Geometry, MatchBasis,
    Observation, Operation, Scene, SceneTime, TimeSlice,
};
use tokio::sync::{mpsc, watch};
use workspace::{LiveOverlay, LiveVideoContent, ResolvedCamera};

use super::{
    bridge::Waiting,
    connector::{Broker, Connector, Decoded, Docs, Inputs, Transport, UnitDecoder},
};

/// What a test pushes into one RTSP connection and reads of it.
pub struct VideoWire {
    pub units: mpsc::Sender<AccessUnit>,
    pub docs: Option<mpsc::Sender<Feed<MetadataDoc>>>,
    pub status: watch::Sender<Status>,
    pub closed: Arc<AtomicBool>,
    /// Set when closing the connection has begun, which [`FakeConnector::hold_closing`] can keep from finishing.
    pub closing: Arc<AtomicBool>,
}

/// What a test pushes into one MQTT or DataHub connection and reads of it.
pub struct MessageWire {
    pub messages: mpsc::Sender<Feed<Message>>,
    pub status: watch::Sender<Status>,
    pub closed: Arc<AtomicBool>,
}

#[derive(Default)]
struct World {
    videos: Mutex<Vec<Arc<VideoWire>>>,
    messages: Mutex<Vec<Arc<MessageWire>>>,
    /// The cameras whose MQTT client a connection holds.
    leased: Mutex<HashSet<String>>,
    /// While set, closing an RTSP connection does not finish.
    closing_held: watch::Sender<bool>,
}

/// A connector over channels the test drives. Connections opened through it are listed in the order they opened.
#[derive(Clone, Default)]
pub struct FakeConnector(Arc<World>);

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

impl FakeConnector {
    /// Keeps the closing of RTSP connections from finishing (or lets it).
    pub fn hold_closing(&self, held: bool) {
        self.0.closing_held.send_replace(held);
    }

    /// The RTSP connections opened so far.
    pub fn videos(&self) -> Vec<Arc<VideoWire>> {
        lock(&self.0.videos).clone()
    }

    /// The MQTT and DataHub connections opened so far that were not refused.
    pub fn messages(&self) -> Vec<Arc<MessageWire>> {
        lock(&self.0.messages).clone()
    }

    fn message_transport(
        &self,
        wire: Arc<MessageWire>,
        rx: mpsc::Receiver<Feed<Message>>,
        waiting: Waiting,
        release: Option<String>,
    ) -> Transport {
        let status = wire.status.subscribe();
        let world = Arc::clone(&self.0);
        lock(&self.0.messages).push(Arc::clone(&wire));
        Transport::new(
            Inputs {
                video: None,
                docs: Some(Docs::Messages {
                    messages: rx,
                    waiting,
                }),
                status,
            },
            async move {
                // Giving the camera back takes a few round trips.
                for _ in 0..8 {
                    tokio::task::yield_now().await;
                }
                if let Some(host) = release {
                    lock(&world.leased).remove(&host);
                }
                wire.closed.store(true, Ordering::Release);
            },
        )
    }
}

impl Connector for FakeConnector {
    fn video(&self, _camera: &ResolvedCamera, metadata: bool) -> Transport {
        let (units, unit_rx) = mpsc::channel(64);
        let (docs, docs_rx) = if metadata {
            let (tx, rx) = mpsc::channel(512);
            (Some(tx), Some(rx))
        } else {
            (None, None)
        };
        let (status_tx, status) = watch::channel(Status::Connecting);
        let wire = Arc::new(VideoWire {
            units,
            docs,
            status: status_tx,
            closed: Arc::default(),
            closing: Arc::default(),
        });
        lock(&self.0.videos).push(Arc::clone(&wire));
        let mut held = self.0.closing_held.subscribe();
        Transport::new(
            Inputs {
                video: Some(unit_rx),
                docs: docs_rx.map(Docs::Metadata),
                status,
            },
            async move {
                wire.closing.store(true, Ordering::Release);
                let _ = held.wait_for(|held| !*held).await;
                wire.closed.store(true, Ordering::Release);
            },
        )
    }

    fn mqtt(&self, camera: &ResolvedCamera, _broker: &Broker, _source_key: &str) -> Transport {
        let (messages, rx) = mpsc::channel(512);
        let held = lock(&self.0.leased).insert(camera.host.clone());
        if !held {
            let busy = CameraError::fake(ErrorKind::Busy, "another subscription holds the camera");
            let (_, status) = watch::channel(Status::Failed(busy));
            return Transport::new(
                Inputs {
                    video: None,
                    docs: None,
                    status,
                },
                async {},
            );
        }
        let wire = Arc::new(MessageWire {
            messages,
            status: watch::channel(Status::Connecting).0,
            closed: Arc::default(),
        });
        self.message_transport(wire, rx, Waiting::Newest, Some(camera.host.clone()))
    }

    fn datahub(&self, _camera: &ResolvedCamera, _topic: &str, _channel: u32) -> Transport {
        let (messages, rx) = mpsc::channel(512);
        let wire = Arc::new(MessageWire {
            messages,
            status: watch::channel(Status::Connecting).0,
            closed: Arc::default(),
        });
        self.message_transport(wire, rx, Waiting::Queue, None)
    }

    fn decoder(
        &self,
        _codec: VideoCodec,
        _parameter_sets: &[u8],
    ) -> Result<Box<dyn UnitDecoder>, UserError> {
        Ok(Box::new(FakeCodec::default()))
    }
}

/// A codec for units that are one byte: the luma of a 2x2 picture. A unit without a byte cannot be decoded. A unit of
/// two bytes (see [`held_back`]) is a picture the codec keeps until the next unit, as one reordering frames does.
#[derive(Default)]
struct FakeCodec {
    held: Option<(u8, FrameId)>,
}

impl UnitDecoder for FakeCodec {
    fn decode<'a>(
        &'a mut self,
        unit: Bytes,
        id: FrameId,
    ) -> Pin<Box<dyn Future<Output = Decoded> + Send + 'a>> {
        Box::pin(async move {
            let (unit, id) = match (unit.len(), self.held.take()) {
                (2, held) => {
                    self.held = Some((unit[0], id));
                    match held {
                        Some((luma, id)) => (Bytes::from(vec![luma]), id),
                        None => return Ok(None),
                    }
                }
                (_, _) => (unit, id),
            };
            if unit.is_empty() {
                return Err(FrameFailure {
                    sequence_id: id.sequence_id,
                    error: UserError::new(ErrorCode::DecodeFailed, "A damaged picture"),
                });
            }
            let plane = |stride, len| Plane { stride, len };
            let layout = FrameLayout {
                width: 2,
                height: 2,
                format: PixelFormat::I420,
                color: ColorDesc {
                    matrix: Matrix::Bt709,
                    full_range: false,
                },
                planes: [plane(2, 4), plane(1, 1), plane(1, 1)],
            };
            let luma = unit.first().copied().unwrap_or(0);
            let data = Bytes::from(vec![luma, luma, luma, luma, 128, 128]);
            VideoFrame::new(id, true, layout, data)
                .map(Some)
                .map_err(|error| FrameFailure {
                    sequence_id: id.sequence_id,
                    error,
                })
        })
    }
}

/// A video unit of connection `epoch` that cannot be decoded, captured at `capture_us` microseconds.
pub fn damaged(epoch: u32, capture_us: i64) -> AccessUnit {
    AccessUnit {
        data: Bytes::new(),
        ..unit(epoch, Some(capture_us), 0)
    }
}

/// Like [`unit`], but its picture comes out of the decoder with the next unit, under its own id.
pub fn held_back(epoch: u32, capture_us: i64, luma: u8) -> AccessUnit {
    AccessUnit {
        data: Bytes::from(vec![luma, 0]),
        ..unit(epoch, Some(capture_us), luma)
    }
}

/// A video unit of connection `epoch` captured at `capture_us` microseconds, whose picture has luma `luma`.
pub fn unit(epoch: u32, capture_us: Option<i64>, luma: u8) -> AccessUnit {
    AccessUnit {
        epoch,
        codec: Arc::new(CodecParams {
            codec: VideoCodec::H264,
            parameter_sets: Bytes::from_static(&[0, 0, 0, 1, 0x67]),
        }),
        keyframe: true,
        capture: capture_us.map(|us| CaptureTime(us * 1000)),
        data: Bytes::from(vec![luma]),
    }
}

/// A holdable gate for the test decoder: while closed, the decoder reads nothing.
#[derive(Default)]
pub struct Hold {
    closed: Mutex<bool>,
    changed: Condvar,
}

impl Hold {
    /// Makes the decoder wait before its next record.
    pub fn close(&self) {
        *lock(&self.closed) = true;
    }

    /// Lets the decoder go on.
    pub fn open(&self) {
        *lock(&self.closed) = false;
        self.changed.notify_all();
    }

    fn wait(&self, cancel: &CancelToken) -> bool {
        let mut closed = lock(&self.closed);
        while *closed {
            if cancel.is_cancelled() {
                return false;
            }
            closed = self
                .changed
                .wait_timeout(closed, Duration::from_millis(10))
                .unwrap_or_else(PoisonError::into_inner)
                .0;
        }
        true
    }
}

static SPEC: LazyLock<Spec> = LazyLock::new(|| Spec {
    id: "LIVE_TEST".into(),
    plugin: "test".into(),
    name: "Live test".into(),
    extensions: Vec::new(),
    description: None,
    framing: Framing::Records,
    bases: vec![MatchBasis::Time],
    default_basis: MatchBasis::Time,
    options: Form::default(),
    version: 1,
});

/// A decoder of records `<microseconds>` (a Scene with one box at that time), `<microseconds> empty` and
/// `<microseconds> events` (a Scene with one event).
pub struct TestDecoder(pub Arc<Hold>);

impl TestDecoder {
    /// The id the handler registers under.
    pub const ID: &'static str = "LIVE_TEST";
}

fn scene_of(line: &str) -> Scene {
    let mut parts = line.split(' ');
    let time = SceneTime::Timestamp(MediaTime(parts.next().unwrap_or("0").parse().unwrap_or(0)));
    let scene = Scene::new(TimeSlice::at(time));
    match parts.next() {
        Some("empty") => return scene,
        Some("events") => {
            let delete = Operation::Delete {
                entity: EntityId::from(1u64),
            };
            return scene
                .with_events(vec![Event::new(TimeSlice::at(time), delete)])
                .expect("a scene");
        }
        _ => {}
    }
    let mut entity = Entity::new(EntityId::from(1u64));
    entity.push_observation(Observation::new(
        time,
        Geometry::Box(BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box")),
    ));
    scene.with_entities(vec![entity]).expect("a scene")
}

impl Decoder for TestDecoder {
    fn spec(&self) -> &Spec {
        &SPEC
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        while let Some(record) = input.next(cancel)? {
            if !self.0.wait(cancel) {
                return Err(Interrupt::Cancelled);
            }
            let line = std::str::from_utf8(record.bytes).unwrap_or_default();
            let _ = sink.sample(scene_of(line), OnEqualKey::Replace);
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "",
                options: Vec::new(),
            },
            metadata: Default::default(),
            coverage: Coverage::Sparse,
        })
    }
}

/// A document the test decoder reads.
pub fn doc(epoch: u32, text: &str) -> Feed<MetadataDoc> {
    Feed::Item(MetadataDoc {
        epoch,
        capture: None,
        xml: Bytes::from(text.to_owned()),
    })
}

pub fn content(host: &str, overlay: LiveOverlay) -> LiveVideoContent {
    let item = foundation::ItemId::generate();
    LiveVideoContent {
        item_id: item,
        id: workspace::ContentId::of_item(&item),
        label: host.to_owned(),
        camera: ResolvedCamera {
            host: host.to_owned(),
            protocol: workspace::Protocol::Https,
            port: 443,
            rtsp_port: 554,
            rtsp_route: workspace::RtspRoute::Direct,
            head: 1,
            resolution: None,
            url: None,
            login: Some(workspace::Login {
                user: "root".into(),
                password: workspace::Secret::new("hunter2"),
            }),
        },
        overlay,
        slot: "live".into(),
    }
}

pub fn rtsp() -> LiveOverlay {
    LiveOverlay::Rtsp {
        handler: TestDecoder::ID.into(),
    }
}

pub fn mqtt(source_key: &str) -> LiveOverlay {
    LiveOverlay::Mqtt {
        handler: TestDecoder::ID.into(),
        broker_host: "broker".into(),
        broker_port: 1883,
        broker_user: String::new(),
        broker_pass: workspace::Secret::new(""),
        source_key: source_key.into(),
    }
}

/// How the live lane matches in these tests: by time, with the default tolerance, nothing kept.
pub fn matching() -> MatchPolicy {
    MatchPolicy {
        basis: Some(Basis::Time),
        sticky: Sticky {
            enabled: false,
            timeout: None,
        },
        ..MatchPolicy::default()
    }
}
