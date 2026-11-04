//! Live camera sources: a viewer opens a camera, and a [`Feed`](feed::Feed) pipeline connects it, holds the video
//! until its overlay is settled (or 1.5 s), decodes it and hands every viewer the newest frame with what its lane
//! selects. Connections go through a [`Connector`]; nothing contacts a host until a viewer opens (the sidebar's
//! snapshots and login checks, in [`crate::glance`], wait for a host a viewer opened for or the user confirmed).
//!
//! Rules: a camera has one MQTT client, so two feeds that need it cannot both run; a feed is shared by every viewer of
//! the same resolved spec and ends with the last one, closing every connection, including the temporary MQTT
//! publisher, before the app exits. A credential is never logged or sent to a viewer: they see a state and a
//! [`LiveReason`](api_types::live::LiveReason).

mod bridge;
mod connector;
#[cfg(test)]
pub(crate) mod fake;
mod feed;
mod overlay;
mod real;
mod source;
mod status;
#[cfg(test)]
mod tests;
mod video;
mod viewer;

pub use connector::{
    Broker, Closer, Connector, Decoded, Docs, Inputs, Transport, UnitDecoder, reason,
};
pub use real::CameraConnector;
pub(crate) use real::camera_of;
pub use real::timeout;
pub use source::{LiveSources, Spec};
pub use status::{STALL_AFTER, STALL_CHECK};
pub use viewer::{LiveViewer, Shown, Update};
