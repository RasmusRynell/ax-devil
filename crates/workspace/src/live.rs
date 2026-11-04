//! The Live Stream Item: a camera's connection settings, checked the same way when the item is made and when a saved
//! one resolves, and expanded into the Content a viewer opens.
//!
//! Connection text is stored as entered: a `$NAME` reference stays one and is expanded only by resolving, where a
//! credential reference needs its host binding or the user's confirmation (docs/rebuild/13-security-packaging-ops.md).

use crate::{
    content::{Content, ContentId},
    credentials::{Confirmation, Environment, Secret, reference},
};
use foundation::{ErrorCode, ItemId, Location, UserError};
use handlers::Handlers;

/// The lane slot of the one overlay a live video has.
pub const LIVE_SLOT: &str = "live";

/// The protocol of the camera's web interface and DataHub.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
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
    pub head: u8,
    /// The RTSP port.
    pub rtsp_port: u16,
    /// The resolution such as `1280x720`; `None` is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the one made from the host; it holds no login.
    pub url: Option<String>,
    /// The overlay.
    pub overlay: LiveOverlay,
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
    /// The video channel, from 1.
    pub head: u8,
    /// The resolution; `None` is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the generated one; it holds no login.
    pub url: Option<String>,
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
    /// The name rows and viewers show: the expanded host, or `Live stream`.
    pub label: String,
    /// The camera to connect to.
    pub camera: ResolvedCamera,
    /// The overlay, with the broker's text expanded.
    pub overlay: LiveOverlay,
    /// The lane slot of the overlay, [`LIVE_SLOT`].
    pub slot: String,
}

fn invalid(field: &str, reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::WorkspaceInvalid, reason).at(Location::field(field))
}

/// The host and port of a stream URL, the host lowercase, or why the URL cannot be used. No port is RTSP's 554.
pub(crate) fn url_endpoint(url: &str) -> Result<(String, u16), UserError> {
    let field = "url";
    let lower = url.to_ascii_lowercase();
    // The camera crate speaks plain RTSP only; an rtsps:// URL would retry forever.
    let Some(rest) = lower.strip_prefix("rtsp://") else {
        return Err(invalid(field, "The stream URL must start with rtsp://."));
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    if authority.contains('@') {
        return Err(invalid(field, "The stream URL must not contain a login."));
    }
    let (host, port) = match authority.strip_prefix('[') {
        Some(inside) => inside
            .split_once(']')
            .map(|(ip, after)| (format!("[{ip}]"), after.strip_prefix(':'))),
        None => Some(match authority.split_once(':') {
            Some((host, port)) => (host.to_owned(), Some(port)),
            None => (authority.to_owned(), None),
        }),
    }
    .filter(|(host, _)| !host.is_empty())
    .ok_or_else(|| invalid(field, "The stream URL has no host."))?;
    let port = match port {
        None => 554,
        Some(port) => port
            .parse()
            .map_err(|_| invalid(field, "The stream URL has no valid port."))?,
    };
    Ok((host, port))
}

/// The host of a stream URL, lowercase, or why the URL cannot be used.
fn url_host(url: &str) -> Result<String, UserError> {
    url_endpoint(url).map(|(host, _)| host)
}

impl LiveRecipe {
    /// The host and port the stream's RTSP requests, and so its login, go to: the stream URL's, or the camera's.
    pub(crate) fn rtsp_endpoint(&self) -> (String, u16) {
        self.url
            .as_deref()
            .and_then(|url| url_endpoint(url).ok())
            .unwrap_or_else(|| (self.host.trim().to_ascii_lowercase(), self.rtsp_port))
    }

    /// The name shown when the item has none: the host as entered, or `Live stream` when it is a reference.
    pub fn default_name(&self) -> String {
        match reference(&self.host) {
            Some(_) => "Live stream".to_owned(),
            None => self.host.clone(),
        }
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
        if let Some(url) = &self.url {
            url_host(url)?;
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
        Ok(())
    }

    /// The references this recipe would send to a host that neither their binding nor the user's confirmation allows:
    /// what to ask the user before starting it. A reference whose variable is unset sends nothing and is not listed.
    pub fn needs_confirmation(&self, env: &Environment) -> Vec<Confirmation> {
        let mut pending = Vec::new();
        self.expand(env, &mut pending);
        pending
    }

    /// The recipe with its references expanded, and in `pending` the ones not allowed to be.
    fn expand(
        &self,
        env: &Environment,
        pending: &mut Vec<Confirmation>,
    ) -> (Expanded, LiveOverlay) {
        let url_host = self.url.as_deref().and_then(|url| url_host(url).ok());
        let camera = Endpoint {
            host: &self.host,
            user: &self.username,
            password: &self.password,
            names: CAMERA_NAMES,
            also_to: url_host.as_deref(),
        };
        let camera = camera.expand(env, pending);
        let overlay = match &self.overlay {
            LiveOverlay::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => {
                let broker = Endpoint {
                    host: broker_host,
                    user: broker_user,
                    password: broker_pass,
                    names: BROKER_NAMES,
                    // The MQTT lease hands the broker login to the camera, which connects to the broker itself.
                    also_to: Some(camera.host.as_str()).filter(|host| !host.is_empty()),
                }
                .expand(env, pending);
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

    /// The Content of the item `item`. Fails like [`check`](Self::check), when the expanded host is empty, and with
    /// [`ErrorCode::NeedsConfirmation`] naming the first reference and host that need the user's confirmation.
    pub(crate) fn resolve(
        &self,
        item: &ItemId,
        handlers: &Handlers,
        env: &Environment,
    ) -> Result<Content, UserError> {
        let at_item = |error: UserError| UserError {
            location: Some(Location::Item(*item)),
            ..error
        };
        self.check(handlers).map_err(at_item)?;
        let mut pending = Vec::new();
        let (camera, overlay) = self.expand(env, &mut pending);
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
        if let Some(Confirmation { reference, host }) = pending.first() {
            return Err(UserError {
                confirmations: pending.clone(),
                ..UserError::new(
                    ErrorCode::NeedsConfirmation,
                    format!("${reference} would be sent to {host}; confirm it first."),
                )
                .at(*item)
            });
        }
        let login = (!camera.user.is_empty() || !camera.password.is_empty()).then_some(Login {
            user: camera.user,
            password: camera.password,
        });
        Ok(Content::LiveVideo(Box::new(LiveVideoContent {
            item_id: *item,
            id: ContentId::of_item(item),
            label: camera.host.clone(),
            camera: ResolvedCamera {
                protocol: self.protocol,
                port: match self.port {
                    0 => self.protocol.default_port(),
                    port => port,
                },
                rtsp_port: self.rtsp_port,
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

/// The environment variables that belong together as one endpoint's host, user and password.
pub(crate) struct Names {
    pub(crate) host: &'static str,
    pub(crate) user: &'static str,
    pub(crate) password: &'static str,
}

pub(crate) const CAMERA_NAMES: Names = Names {
    host: "AX_DEVIL_TARGET_ADDR",
    user: "AX_DEVIL_TARGET_USER",
    password: "AX_DEVIL_TARGET_PASS",
};

pub(crate) const BROKER_NAMES: Names = Names {
    host: "AX_DEVIL_MQTT_BROKER_ADDR",
    user: "AX_DEVIL_MQTT_BROKER_USER",
    password: "AX_DEVIL_MQTT_BROKER_PASS",
};

/// A host with its login as entered, and where the login is sent.
pub(crate) struct Endpoint<'a> {
    pub(crate) host: &'a str,
    pub(crate) user: &'a str,
    pub(crate) password: &'a Secret,
    pub(crate) names: Names,
    /// A second host that gets the login: the host of a custom stream URL, or the camera for a broker login.
    pub(crate) also_to: Option<&'a str>,
}

pub(crate) struct Expanded {
    pub(crate) host: String,
    pub(crate) user: String,
    pub(crate) password: Secret,
}

impl Endpoint<'_> {
    /// Expands the three texts. A reference expands for free only as the variable bound to its role and only when the
    /// host is the reference bound to the host role and the login goes nowhere else; any other expands only for a host
    /// the user confirmed it for. What is not allowed is still expanded, and listed in `pending`.
    pub(crate) fn expand(&self, env: &Environment, pending: &mut Vec<Confirmation>) -> Expanded {
        let host = env.expand(self.host);
        let together = reference(self.host) == Some(self.names.host);
        let mut ask = |text: &str, value: &str, bound: &str, destinations: &[&str]| {
            let Some(name) = reference(text).filter(|_| !value.is_empty()) else {
                return;
            };
            for destination in destinations {
                let free = together && name == bound && destination.eq_ignore_ascii_case(&host);
                let asked = |c: &Confirmation| c.reference == name && c.host == *destination;
                if !free && !env.is_confirmed(name, destination) && !pending.iter().any(asked) {
                    pending.push(Confirmation {
                        reference: name.to_owned(),
                        host: (*destination).to_owned(),
                    });
                }
            }
        };
        ask(self.host, &host, self.names.host, &[&host]);
        let mut destinations = vec![host.as_str()];
        destinations.extend(self.also_to.filter(|to| !to.eq_ignore_ascii_case(&host)));
        let user = env.expand(self.user);
        ask(self.user, &user, self.names.user, &destinations);
        let password = env.expand(self.password.expose());
        ask(
            self.password.expose(),
            &password,
            self.names.password,
            &destinations,
        );
        Expanded {
            host,
            user,
            password: Secret::new(password),
        }
    }
}
