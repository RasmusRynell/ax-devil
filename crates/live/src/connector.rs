//! The seam to everything outside the process: what a feed gets from a camera or a broker, and the codec that turns
//! its video into pictures. The real one is `real.rs`; tests use `fake.rs`.
//!
//! A [`Transport`] is one connection as the camera crate hands it out: its channels, its status and the future that
//! closes it. Camera types stay inside this crate; what reaches the rest of the app is wire types.

use std::{future::Future, pin::Pin};

use api_types::live::LiveReason;
use bytes::Bytes;
use camera::{AccessUnit, ErrorKind, Feed, Message, MetadataDoc, Refusal, Status, VideoCodec};
use foundation::{FrameFailure, FrameId, UserError, VideoFrame};
use tokio::sync::{mpsc, watch};
use workspace::{Login, ResolvedCamera};

use super::bridge::Waiting;

/// Ends a connection and waits until it is closed; for MQTT that includes giving back what was set up on the camera.
pub type Closer = Pin<Box<dyn Future<Output = ()> + Send>>;

/// What a connection delivers besides video.
pub enum Docs {
    /// ONVIF metadata documents of the RTSP stream.
    Metadata(mpsc::Receiver<Feed<MetadataDoc>>),
    /// MQTT messages or DataHub samples.
    Messages {
        /// The messages or samples.
        messages: mpsc::Receiver<Feed<Message>>,
        /// How those waiting for the decoder are kept.
        waiting: Waiting,
    },
}

impl Docs {
    /// How the documents waiting for the decoder are kept.
    pub fn waiting(&self) -> Waiting {
        match self {
            Docs::Metadata(_) => Waiting::Queue,
            Docs::Messages { waiting, .. } => *waiting,
        }
    }
}

/// What a connection delivers and how it stands.
pub struct Inputs {
    /// Video units, for an RTSP connection.
    pub video: Option<mpsc::Receiver<AccessUnit>>,
    /// Overlay documents, for an RTSP connection that asked for metadata, MQTT and DataHub.
    pub docs: Option<Docs>,
    /// How the connection stands.
    pub status: watch::Receiver<Status>,
}

/// One connection to a camera or a broker.
pub struct Transport {
    inputs: Inputs,
    close: Closer,
}

impl Transport {
    /// A connection that delivers `inputs` and is ended by awaiting `close`.
    pub fn new(inputs: Inputs, close: impl Future<Output = ()> + Send + 'static) -> Self {
        Self {
            inputs,
            close: Box::pin(close),
        }
    }

    /// What it delivers, and the future that closes it. The closer is held by the feed, so every connection is closed
    /// whatever its readers did.
    pub fn split(self) -> (Inputs, Closer) {
        (self.inputs, self.close)
    }
}

/// An MQTT broker as the camera and this machine both reach it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Broker {
    /// Its host.
    pub host: String,
    /// Its TCP port.
    pub port: u16,
    /// The login; `None` for none.
    pub login: Option<Login>,
}

/// What decoding one unit gives: a picture, nothing yet, or why the unit is lost.
pub type Decoded = Result<Option<VideoFrame>, FrameFailure>;

/// A codec for one video stream. Units come in decode order; after damage it skips to the next keyframe on its own.
pub trait UnitDecoder: Send {
    /// Decodes `unit`, the picture `id`. May take as long as the codec needs; the caller does nothing else with the
    /// stream meanwhile.
    fn decode<'a>(
        &'a mut self,
        unit: Bytes,
        id: FrameId,
    ) -> Pin<Box<dyn Future<Output = Decoded> + Send + 'a>>;
}

/// Everything a feed needs from outside the process. Opening a connection returns at once: nothing here waits on a
/// camera, and a connection that cannot be made reports it in its status.
pub trait Connector: Send + Sync + 'static {
    /// The RTSP stream of `camera`, with the ONVIF metadata when `metadata` is set.
    fn video(&self, camera: &ResolvedCamera, metadata: bool) -> Transport;

    /// The analytics `source_key` of `camera` through `broker`. A camera has one MQTT client, so a second connection
    /// to the same camera fails `Busy` until the first is closed.
    fn mqtt(&self, camera: &ResolvedCamera, broker: &Broker, source_key: &str) -> Transport;

    /// The DataHub `topic` on `channel` of `camera`.
    fn datahub(&self, camera: &ResolvedCamera, topic: &str, channel: u32) -> Transport;

    /// A codec for `codec`, primed with `parameter_sets` (Annex B).
    fn decoder(
        &self,
        codec: VideoCodec,
        parameter_sets: &[u8],
    ) -> Result<Box<dyn UnitDecoder>, UserError>;
}

/// The code the viewer words for a failure of `kind`.
pub fn reason(kind: ErrorKind) -> LiveReason {
    match kind {
        ErrorKind::LoginRequired => LiveReason::LoginRequired,
        ErrorKind::LoginRejected => LiveReason::LoginRejected,
        ErrorKind::Forbidden => LiveReason::Forbidden,
        ErrorKind::NotAxis => LiveReason::NotAxis,
        ErrorKind::NotFound => LiveReason::NotFound,
        ErrorKind::Unsupported => LiveReason::Unsupported,
        ErrorKind::SourceUnavailable => LiveReason::SourceUnavailable,
        ErrorKind::BrokerRefused(Refusal::UnsupportedProtocol) => {
            LiveReason::BrokerUnsupportedProtocol
        }
        ErrorKind::BrokerRefused(Refusal::ClientIdRejected) => LiveReason::BrokerClientIdRejected,
        ErrorKind::BrokerRefused(Refusal::BadCredentials) => LiveReason::BrokerBadCredentials,
        ErrorKind::BrokerRefused(Refusal::NotAuthorized) => LiveReason::BrokerNotAuthorized,
        ErrorKind::Busy => LiveReason::Busy,
        ErrorKind::Unrestorable => LiveReason::Unrestorable,
        ErrorKind::InvalidTarget => LiveReason::InvalidTarget,
        ErrorKind::Unreachable => LiveReason::Unreachable,
        ErrorKind::TimedOut => LiveReason::TimedOut,
        ErrorKind::Lost => LiveReason::Lost,
        ErrorKind::Silent => LiveReason::Silent,
        ErrorKind::Malformed => LiveReason::Malformed,
        ErrorKind::Throttled => LiveReason::Throttled,
        ErrorKind::DeviceFault => LiveReason::DeviceFault,
    }
}
