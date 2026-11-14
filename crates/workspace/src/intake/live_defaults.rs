//! The configured values a new Live Stream Item takes for what the user left empty.

use crate::{
    credentials::Secret,
    live::{Protocol, RtspRoute},
};

/// What overlay a new stream gets when the user did not choose.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LiveMode {
    /// Video only.
    None,
    /// Metadata inside the RTSP stream.
    Rtsp,
    /// An MQTT broker.
    Mqtt,
    /// A DataHub topic.
    DataHub,
}

/// The MQTT overlay a new stream starts from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MqttDefaults {
    /// The decoder.
    pub handler: String,
    /// The broker host, as written (a `$NAME` reference stays one).
    pub host: String,
    /// The broker user, as written.
    pub user: String,
    /// The broker password, as written.
    pub password: Secret,
    /// The broker's TCP port.
    pub port: u16,
    /// The camera's analytics data source.
    pub source_key: String,
}

/// The DataHub overlay a new stream starts from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DataHubDefaults {
    /// The decoder.
    pub handler: String,
    /// The topic.
    pub topic: String,
    /// The topic's instance.
    pub channel: u32,
}

/// The configured values a new Live Stream Item takes for what the user left empty. Connection text is as written in
/// the settings, so a `$NAME` reference stays one in the item; the values are copied in and the item never refers back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveDefaults {
    /// The camera host, as written.
    pub host: String,
    /// The camera user, as written.
    pub username: String,
    /// The camera password, as written.
    pub password: Secret,
    /// The web interface's protocol.
    pub protocol: Protocol,
    /// The video channel.
    pub head: u16,
    /// The RTSP port.
    pub rtsp_port: u16,
    /// Which door of the camera a stream comes through.
    pub rtsp_route: RtspRoute,
    /// The resolution; empty is the camera's own.
    pub resolution: String,
    /// The overlay a stream gets unless the user chose one.
    pub overlay: LiveMode,
    /// The decoder of the RTSP overlay.
    pub rtsp_handler: String,
    /// The MQTT overlay.
    pub mqtt: MqttDefaults,
    /// The DataHub overlay.
    pub datahub: DataHubDefaults,
}

impl LiveDefaults {
    /// The defaults without the passwords. A stream someone other than the settings' owner adds takes these, so a
    /// password the owner configured is never filled into a login for a host that person names.
    #[must_use]
    pub fn without_secrets(&self) -> Self {
        let mut open = self.clone();
        open.password = Secret::new("");
        open.mqtt.password = Secret::new("");
        open
    }

    /// The defaults for editing a stream, which has every value of its own: the numbers stay, and every text is empty,
    /// so a field the person cleared stays cleared instead of taking the configured text.
    #[must_use]
    pub fn for_edit(&self) -> Self {
        let mut edit = self.without_secrets();
        edit.host.clear();
        edit.username.clear();
        edit.resolution.clear();
        edit.rtsp_handler.clear();
        edit.mqtt.handler.clear();
        edit.mqtt.host.clear();
        edit.mqtt.user.clear();
        edit.mqtt.source_key.clear();
        edit.datahub.handler.clear();
        edit.datahub.topic.clear();
        edit
    }
}
