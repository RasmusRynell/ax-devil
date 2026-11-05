//! Everything that talks to an Axis camera, and nothing from the app: the device API (identify, sign in, snapshot,
//! resolutions, analytics sources), RTSP video and metadata, MQTT analytics, DataHub samples, and the values every connection shares.
//! See the README for the rules.

mod auth;
mod datahub;
mod device;
mod error;
mod live;
mod mqtt;
mod rtsp;
mod scan;
mod target;
mod tls;
mod vapix;

pub use datahub::open_datahub;
pub use device::Device;
pub use error::{CameraError, ErrorKind, Refusal};
pub use live::{
    AccessUnit, CaptureTime, CodecParams, DataHubStamp, Feed, LiveMessages, LiveStream, Message,
    MetadataDoc, Reconnect, Stats, Status, VideoCodec,
};
pub use mqtt::{Publisher, open_mqtt};
pub use rtsp::{StreamRequest, open_stream};
pub use scan::{Found, MAX_TARGETS, ScanLoad, ScanPorts, SignIn, Targets, check, scan};
pub use target::{Broker, Camera, Head, Host, Login, Scheme, Secret, Web};
pub use url::Url;
pub use vapix::{Identity, Resolution, SourceKey};
