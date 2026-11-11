//! The Live Stream Item: a camera's connection settings, checked the same way when the item is made and when a saved
//! one resolves, and expanded into the Content a viewer opens.
//!
//! Connection text is stored as entered: a `$NAME` reference stays one and is expanded silently by resolving.

use crate::{
    content::{Content, ContentId},
    credentials::{Environment, Secret, reference},
    host::shown_host,
};
use foundation::{ErrorCode, ItemId, Location, UserError};
use handlers::Handlers;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

mod endpoint;
mod stream_url;

pub(crate) use endpoint::{camera_endpoint, url_endpoint, url_has_login};
pub use endpoint::{url_password_dropped, url_without_password};
pub use stream_url::StreamUrl;

/// The lane slot of the one overlay a live video has.
pub const LIVE_SLOT: &str = "live";

/// The last camera head a stream can name.
const HEAD_MAX: u16 = 9999;

/// The protocol of the camera's web interface and DataHub.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
#[ts(rename = "LiveProtocol")]
pub enum Protocol {
    /// Plain HTTP.
    Http,
    /// HTTP over TLS.
    Https,
}

impl Protocol {
    /// The standard port.
    pub const fn default_port(self) -> u16 {
        match self {
            Self::Http => 80,
            Self::Https => 443,
        }
    }

    /// The name users read: `HTTP` or `HTTPS`.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Http => "HTTP",
            Self::Https => "HTTPS",
        }
    }
}

/// Which door of the camera a stream comes through, when it has no stream URL of its own (the URL's scheme decides).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
#[ts(rename = "LiveRtspRoute")]
pub enum RtspRoute {
    /// The camera's RTSP port.
    #[default]
    Direct,
    /// The camera's web interface, for a network that blocks the RTSP port.
    WebApi,
}

impl RtspRoute {
    /// The name users read: `Direct` or `Via web API`.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Direct => "Direct",
            Self::WebApi => "Via web API",
        }
    }
}

/// Where a live video's overlay comes from. At most one; its handler is a decoder id. In a recipe the broker text is as
/// entered; in Content it is expanded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LiveOverlay {
    /// Video only.
    None,
    /// Analytics metadata inside the RTSP stream.
    Rtsp {
        /// The decoder.
        handler: String,
    },
    /// Analytics the camera publishes to an MQTT broker.
    Mqtt {
        /// The decoder.
        handler: String,
        /// The broker's host.
        broker_host: String,
        /// The broker's TCP port.
        broker_port: u16,
        /// The broker login's user; empty for none.
        broker_user: String,
        /// The broker login's password; empty for none.
        broker_pass: Secret,
        /// The camera's analytics data source to publish.
        source_key: String,
    },
    /// A DataHub topic of the camera.
    DataHub {
        /// The decoder.
        handler: String,
        /// The topic.
        topic: String,
        /// The topic's instance.
        channel: u32,
    },
}

impl LiveOverlay {
    /// What the mode is called where a message names it.
    pub const fn mode_name(&self) -> &'static str {
        match self {
            Self::None => "None",
            Self::Rtsp { .. } => "RTSP Embedded",
            Self::Mqtt { .. } => "MQTT",
            Self::DataHub { .. } => "DataHub WebSocket",
        }
    }

    /// The decoder id, when there is an overlay.
    pub fn handler(&self) -> Option<&str> {
        match self {
            Self::None => None,
            Self::Rtsp { handler } | Self::Mqtt { handler, .. } | Self::DataHub { handler, .. } => {
                Some(handler)
            }
        }
    }
}

/// The MQTT settings of a stream, as the form holds them while another overlay mode is chosen.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MqttSettings {
    /// The broker's host.
    pub broker_host: String,
    /// The broker's TCP port.
    pub broker_port: i64,
    /// The broker login's user; empty for none.
    pub broker_user: String,
    /// The broker login's password; empty for none.
    pub broker_pass: Secret,
    /// The camera's analytics data source.
    pub source_key: String,
}

/// The DataHub settings of a stream, as the form holds them while another overlay mode is chosen.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DataHubSettings {
    /// The topic.
    pub topic: String,
    /// The topic's instance.
    pub channel: i64,
}

/// What the overlay modes not chosen hold, so editing a stream keeps what the person had entered for them. The chosen
/// mode's slot is always `None`; the settings are not checked, since nothing reads them until their mode is chosen.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct KeptTransports {
    /// The MQTT settings, unless MQTT is the overlay.
    pub mqtt: Option<MqttSettings>,
    /// The DataHub settings, unless DataHub is the overlay.
    pub datahub: Option<DataHubSettings>,
}

/// A camera to stream from and the overlay to draw on it, with every text as entered.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveRecipe {
    /// The camera's host.
    pub host: String,
    /// The camera login's user; empty for none.
    pub username: String,
    /// The camera login's password; empty for none.
    pub password: Secret,
    /// The web interface's protocol.
    pub protocol: Protocol,
    /// The web interface's port; 0 is the protocol's standard one.
    pub port: u16,
    /// The video channel, from 1.
    pub head: u16,
    /// The RTSP port.
    pub rtsp_port: u16,
    /// Which door of the camera the stream comes through; a stream URL overrides it.
    pub rtsp_route: RtspRoute,
    /// The resolution such as `1280x720`; `None` is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the one made from the host, as typed: `rtsp://`, `http://` or `https://`, with
    /// a login if it has one. Whether it can be streamed is for the camera to say when it connects.
    pub url: Option<StreamUrl>,
    /// The overlay.
    pub overlay: LiveOverlay,
    /// What the other overlay modes hold.
    pub kept: KeptTransports,
}

/// A user name and password.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Login {
    /// The user.
    pub user: String,
    /// The password.
    pub password: Secret,
}

/// A camera with every reference expanded, ready to connect to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolvedCamera {
    /// The host.
    pub host: String,
    /// The web interface's protocol.
    pub protocol: Protocol,
    /// The web interface's port, never 0.
    pub port: u16,
    /// The RTSP port.
    pub rtsp_port: u16,
    /// Which door of the camera the stream comes through; a stream URL overrides it.
    pub rtsp_route: RtspRoute,
    /// The video channel, from 1.
    pub head: u16,
    /// The resolution; `None` is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the generated one, as typed, with its login if it has one.
    pub url: Option<StreamUrl>,
    /// The login, when there is a user or a password.
    pub login: Option<Login>,
}

/// A live camera stream: what a viewer opens to show it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveVideoContent {
    /// The item it was resolved from.
    pub item_id: ItemId,
    /// Names this Content; the item's id.
    pub id: ContentId,
    /// The item's label, or the expanded host when it has none.
    pub label: String,
    /// The camera to connect to.
    pub camera: ResolvedCamera,
    /// The overlay, with the broker's text expanded.
    pub overlay: LiveOverlay,
    /// The lane slot of the overlay, [`LIVE_SLOT`].
    pub slot: String,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<Protocol>();
    types.visit::<RtspRoute>();
}

fn invalid(field: &str, reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::WorkspaceInvalid, reason).at(Location::field(field))
}

impl LiveRecipe {
    /// The MQTT settings the stream holds: the overlay's, or else the ones kept while another mode is chosen.
    pub fn mqtt_settings(&self) -> Option<MqttSettings> {
        match &self.overlay {
            LiveOverlay::Mqtt {
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
                ..
            } => Some(MqttSettings {
                broker_host: broker_host.clone(),
                broker_port: i64::from(*broker_port),
                broker_user: broker_user.clone(),
                broker_pass: broker_pass.clone(),
                source_key: source_key.clone(),
            }),
            _ => self.kept.mqtt.clone(),
        }
    }

    /// The name shown when the item has none: the host as entered, or `Live stream` when it is a reference.
    pub fn default_name(&self) -> String {
        match reference(&self.host) {
            Some(_) => "Live stream".to_owned(),
            None => self.host.clone(),
        }
    }

    /// The name shown when the item has none: the host as [`expanded_hosts`](Self::expanded_hosts) reads it, or
    /// `Live stream` when there is none.
    pub fn expanded_name(&self, env: &Environment) -> String {
        match shown_host(&self.host, env) {
            host if host.is_empty() => "Live stream".to_owned(),
            host => host,
        }
    }

    /// The camera host and, for an MQTT overlay, the broker host, as they may be shown: text as typed, and a `$NAME`
    /// reference as its value from `env` only when that is a plain host, else empty (an unset variable too); see
    /// [`DisplayHost`].
    pub fn expanded_hosts(&self, env: &Environment) -> (String, Option<String>) {
        let broker = match &self.overlay {
            LiveOverlay::Mqtt { broker_host, .. } => Some(shown_host(broker_host, env)),
            _ => None,
        };
        (shown_host(&self.host, env), broker)
    }

    /// Fails with the field and the reason when the recipe could not be streamed whatever the environment: the same
    /// rules whether the item was just entered or read from a file. Every message names what to fix.
    pub(crate) fn check(&self, handlers: &Handlers) -> Result<(), UserError> {
        if self.host.is_empty() {
            return Err(invalid("host", "Enter the device host."));
        }
        if self.head < 1 {
            return Err(invalid("head", "Camera Head must be a positive integer."));
        }
        if self.head > HEAD_MAX {
            return Err(invalid("head", "Camera Head must be at most 9999."));
        }
        if self.rtsp_port < 1 {
            return Err(invalid(
                "rtsp_port",
                "RTSP Port must be between 1 and 65535.",
            ));
        }
        // The standard port of the other protocol cannot be addressed.
        let crossed = match self.protocol {
            Protocol::Http => 443,
            Protocol::Https => 80,
        };
        if self.port == crossed {
            return Err(invalid(
                "port",
                format!(
                    "{} cannot use port {}; use the standard port.",
                    self.protocol.name(),
                    self.port
                ),
            ));
        }
        // The fields in the order the form lists them: the handler comes after the transport's own.
        match &self.overlay {
            LiveOverlay::Mqtt {
                broker_host,
                broker_port,
                source_key,
                ..
            } => {
                if broker_host.is_empty() {
                    return Err(invalid(
                        "broker_host",
                        "MQTT Host is required for MQTT overlay mode.",
                    ));
                }
                if source_key.is_empty() {
                    return Err(invalid(
                        "source_key",
                        "Data Source is required for MQTT overlay mode.",
                    ));
                }
                if *broker_port < 1 {
                    return Err(invalid(
                        "broker_port",
                        "MQTT Port must be between 1 and 65535.",
                    ));
                }
            }
            LiveOverlay::DataHub { topic, channel, .. } => {
                if topic.is_empty() {
                    return Err(invalid(
                        "topic",
                        "Topic is required for WebSocket overlay mode.",
                    ));
                }
                if *channel < 1 {
                    return Err(invalid("channel", "Channel ID must be a positive integer."));
                }
            }
            LiveOverlay::None | LiveOverlay::Rtsp { .. } => {}
        }
        let mode = self.overlay.mode_name();
        if let Some(handler) = self.overlay.handler() {
            if handler.is_empty() {
                return Err(invalid(
                    "handler",
                    format!("Select a handler type for {mode} overlays."),
                ));
            }
            let Some(decoder) = handlers.decoder(handler) else {
                return Err(UserError::new(
                    ErrorCode::UnknownHandler,
                    format!("Unknown live overlay handler type: {handler}"),
                )
                .at(Location::field("handler")));
            };
            if !decoder.reads_live() {
                return Err(invalid(
                    "handler",
                    format!(
                        "{} cannot decode a live overlay; select another handler type.",
                        decoder.spec().name
                    ),
                ));
            }
        }
        Ok(())
    }

    /// The recipe with its references expanded.
    fn expand(&self, env: &Environment) -> (Expanded, LiveOverlay) {
        let camera = Expanded::of(env, &self.host, &self.username, &self.password);
        let overlay = match &self.overlay {
            LiveOverlay::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => {
                let broker = Expanded::of(env, broker_host, broker_user, broker_pass);
                LiveOverlay::Mqtt {
                    handler: handler.clone(),
                    broker_host: broker.host,
                    broker_port: *broker_port,
                    broker_user: broker.user,
                    broker_pass: broker.password,
                    source_key: source_key.clone(),
                }
            }
            other => other.clone(),
        };
        (camera, overlay)
    }

    /// The Content of the item `item`, labelled `label`, or by the expanded host when the item has none (empty).
    /// Fails like [`check`](Self::check), and when the expanded host is empty.
    pub(crate) fn resolve(
        &self,
        item: &ItemId,
        label: &str,
        handlers: &Handlers,
        env: &Environment,
    ) -> Result<Content, UserError> {
        let at_item = |error: UserError| UserError {
            location: Some(Location::Item(*item)),
            ..error
        };
        self.check(handlers).map_err(at_item)?;
        let (camera, overlay) = self.expand(env);
        if camera.host.is_empty() {
            return Err(at_item(invalid("host", "Enter the device host.")));
        }
        if let LiveOverlay::Mqtt { broker_host, .. } = &overlay
            && broker_host.is_empty()
        {
            return Err(at_item(invalid(
                "broker_host",
                "MQTT Host is required for MQTT overlay mode.",
            )));
        }
        let login = (!camera.user.is_empty() || !camera.password.is_empty()).then_some(Login {
            user: camera.user,
            password: camera.password,
        });
        Ok(Content::LiveVideo(Box::new(LiveVideoContent {
            item_id: *item,
            id: ContentId::of_item(item),
            label: if label.is_empty() {
                camera.host.clone()
            } else {
                label.to_owned()
            },
            camera: ResolvedCamera {
                protocol: self.protocol,
                port: match self.port {
                    0 => self.protocol.default_port(),
                    port => port,
                },
                rtsp_port: self.rtsp_port,
                rtsp_route: self.rtsp_route,
                head: self.head,
                resolution: self.resolution.clone(),
                url: self.url.clone(),
                login,
                host: camera.host,
            },
            overlay,
            slot: LIVE_SLOT.to_owned(),
        })))
    }
}

/// A host with its login, references expanded.
pub(crate) struct Expanded {
    pub(crate) host: String,
    pub(crate) user: String,
    pub(crate) password: Secret,
}

impl Expanded {
    /// The three texts as entered, each replaced by its variable when it is a whole-value `$NAME` reference.
    pub(crate) fn of(env: &Environment, host: &str, user: &str, password: &Secret) -> Self {
        Self {
            host: env.expand(host),
            user: env.expand(user),
            password: Secret::new(env.expand(password.expose())),
        }
    }
}
