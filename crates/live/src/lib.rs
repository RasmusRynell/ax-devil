//! The adapter between the `camera` crate and the app, and the only crate that depends on it. Live camera sources: a
//! viewer opens a camera, and a [`Feed`](feed::Feed) pipeline connects it, holds the video until its overlay is
//! settled (or 1.5 s), decodes it and hands every viewer the newest frame with what its lane selects. Connections go
//! through a [`Connector`]; nothing contacts a host until a viewer opens (the sidebar's snapshots and login checks, in
//! [`glance`], wait for a host a viewer opened for). The network scan ([`network`]) and the form's camera questions
//! ([`camera_questions`]) are the other things the app asks cameras.
//!
//! Rules: a camera has one MQTT client, so two feeds that need it cannot both run; a feed is shared by every viewer of
//! the same resolved spec and ends with the last one, closing every connection, including the temporary MQTT
//! publisher, before the app exits. A credential is never logged or sent to a viewer: they see a state and a
//! [`LiveReason`](api_types::live::LiveReason).

mod bridge;
pub mod camera_questions;
mod connector;
#[cfg(any(test, feature = "fake"))]
pub mod fake;
mod feed;
mod gate;
pub mod glance;
pub mod network;
mod overlay;
mod real;
mod source;
mod status;
#[cfg(test)]
mod tests;
mod throttled;
mod video;
mod viewer;

// The camera items the app's own code names: the crate that depends on `camera` hands them on, so no other does.
pub use camera::{Camera, Head, Host, ScanLoad, Scheme, Targets, Web, retina_log_layer};
pub(crate) use connector::reason;
pub use connector::{Broker, Closer, Connector, Decoded, Docs, Inputs, Transport, UnitDecoder};
pub use real::{camera_of, timeout};
pub use source::LiveSources;
pub use viewer::{LiveViewer, Shown, Update};
