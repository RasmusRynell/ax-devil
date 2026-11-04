//! Intake of a Live Stream Item: what the user entered, with the configured defaults for what they left empty.

use crate::{
    credentials::Secret,
    item::{Recipe, Source, WorkspaceItem},
    live::{LiveOverlay, LiveRecipe, Protocol, url_endpoint},
};
use foundation::{ErrorCode, ItemId, Location, UserError};
use handlers::Handlers;

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
    pub head: u8,
    /// The RTSP port.
    pub rtsp_port: u16,
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
}

/// The MQTT overlay the user chose; empty parts take the defaults.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct NewMqtt {
    /// The decoder id.
    pub handler: Option<String>,
    /// The broker host.
    pub broker_host: Option<String>,
    /// The broker port, checked against 1 to 65535.
    pub broker_port: Option<i64>,
    /// The broker user.
    pub broker_user: Option<String>,
    /// The broker password.
    pub broker_pass: Option<Secret>,
    /// The camera's analytics data source.
    pub source_key: Option<String>,
}

/// The DataHub overlay the user chose; empty parts take the defaults.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct NewDataHub {
    /// The decoder id.
    pub handler: Option<String>,
    /// The topic.
    pub topic: Option<String>,
    /// The topic's instance, from 1.
    pub channel: Option<i64>,
}

/// The overlay the user chose for a new stream.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NewLiveOverlay {
    /// Video only.
    None,
    /// Metadata inside the RTSP stream, read by this decoder id; `None` is the default.
    Rtsp {
        /// The decoder id.
        handler: Option<String>,
    },
    /// An MQTT broker.
    Mqtt(NewMqtt),
    /// A DataHub topic.
    DataHub(NewDataHub),
}

/// What the user entered to add a live stream. Text that is `None` or blank takes its default; numbers are checked
/// against their range here, so the message names the field.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct NewLive {
    /// The name; empty shows the host.
    pub label: Option<String>,
    /// The camera host or a `$NAME` reference.
    pub host: Option<String>,
    /// The camera user or a reference.
    pub username: Option<String>,
    /// The camera password or a reference.
    pub password: Option<Secret>,
    /// The web interface's protocol.
    pub protocol: Option<Protocol>,
    /// The web interface's port, 0 to 65535; 0 or none is the standard one.
    pub port: Option<i64>,
    /// The video channel, from 1.
    pub head: Option<i64>,
    /// The RTSP port, 1 to 65535.
    pub rtsp_port: Option<i64>,
    /// The resolution, such as `1280x720`; blank is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the one made from the host.
    pub url: Option<String>,
    /// The overlay; `None` is the configured one.
    pub overlay: Option<NewLiveOverlay>,
}

impl NewLive {
    /// Fills a blank user and password from the login saved with `source`, when this stream is for the same camera:
    /// the same host text, protocol and port once `defaults` fill what is blank. Another host gets nothing, so a saved
    /// password is never sent where the person who adds the stream did not first see it go. A user entered and not
    /// `source`'s keeps its own password too, which `source`'s does not belong to.
    ///
    /// A stream URL is where the RTSP login goes, so a copy needs it to name the host and port `source` already
    /// sends its login to; any other is refused, as the person must then enter the login for that server.
    pub fn copy_login(
        &mut self,
        source: &LiveRecipe,
        defaults: &LiveDefaults,
    ) -> Result<(), UserError> {
        let protocol = self.protocol.unwrap_or(defaults.protocol);
        let port = |protocol: Protocol, port: i64| match port {
            0 => i64::from(protocol.default_port()),
            port => port,
        };
        let same_camera = text(&self.host, &defaults.host).eq_ignore_ascii_case(source.host.trim())
            && protocol == source.protocol
            && port(protocol, self.port.unwrap_or(0)) == port(source.protocol, source.port.into());
        if !same_camera {
            return Ok(());
        }
        let user_blank = match self.username.as_deref().map(str::trim) {
            Some(user) if !user.is_empty() && user != source.username => return Ok(()),
            Some(user) => user.is_empty(),
            None => true,
        };
        let password_blank = self
            .password
            .as_ref()
            .is_none_or(|entered| entered.expose().trim().is_empty());
        if !user_blank && !password_blank {
            return Ok(());
        }
        if let Some(url) = self
            .url
            .as_deref()
            .map(str::trim)
            .filter(|url| !url.is_empty())
            && url_endpoint(url).ok() != Some(source.rtsp_endpoint())
        {
            return Err(invalid(
                "password",
                "A saved login is not sent to another stream URL; enter the user and password for it.",
            ));
        }
        if user_blank {
            self.username = Some(source.username.clone());
        }
        if password_blank {
            self.password = Some(source.password.clone());
        }
        Ok(())
    }
}

fn invalid(field: &str, reason: &str) -> UserError {
    UserError::new(ErrorCode::WorkspaceInvalid, reason).at(Location::field(field))
}

/// The trimmed `entered` text, or `default` when it is none or blank.
fn text(entered: &Option<String>, default: &str) -> String {
    match entered.as_deref().map(str::trim) {
        Some(entered) if !entered.is_empty() => entered.to_owned(),
        _ => default.to_owned(),
    }
}

fn secret(entered: &Option<Secret>, default: &Secret) -> Secret {
    match entered {
        Some(entered) if !entered.expose().trim().is_empty() => {
            Secret::new(entered.expose().trim())
        }
        _ => default.clone(),
    }
}

/// `number` as `T`: a number below 1 becomes `T`'s zero, which the recipe's own check refuses with the field's
/// message; one too large for `T` is refused here with `too_big`.
fn narrow<T: TryFrom<i64> + Default>(
    number: i64,
    field: &str,
    too_big: &str,
) -> Result<T, UserError> {
    if number < 1 {
        return Ok(T::default());
    }
    T::try_from(number).map_err(|_| invalid(field, too_big))
}

/// Builds the item for the live stream `new`, with `defaults` for what it leaves empty. The item stores the values
/// as entered, `$NAME` references included; whether the host answers, or a reference expands, is for resolving.
///
/// Refuses, with the field as the error's location, what resolving would refuse: no host, a head below 1, ports out
/// of range, the other protocol's standard port, a stream URL that is not RTSP or holds a login, and an overlay
/// without a known decoder or without what its transport needs. A name left empty stays empty, and shows the host.
pub fn live(
    new: NewLive,
    defaults: &LiveDefaults,
    handlers: &Handlers,
) -> Result<WorkspaceItem, UserError> {
    let protocol = new.protocol.unwrap_or(defaults.protocol);
    let port = match new.port {
        None => 0,
        Some(port) => {
            u16::try_from(port).map_err(|_| invalid("port", "Port must be between 0 and 65535."))?
        }
    };
    let head = narrow::<u8>(
        new.head.unwrap_or_else(|| defaults.head.into()),
        "head",
        "Camera Head must be at most 255.",
    )?;
    let rtsp_port = narrow::<u16>(
        new.rtsp_port.unwrap_or_else(|| defaults.rtsp_port.into()),
        "rtsp_port",
        "RTSP Port must be between 1 and 65535.",
    )?;
    let overlay = match new.overlay.clone().unwrap_or(match defaults.overlay {
        LiveMode::None => NewLiveOverlay::None,
        LiveMode::Rtsp => NewLiveOverlay::Rtsp { handler: None },
        LiveMode::Mqtt => NewLiveOverlay::Mqtt(NewMqtt::default()),
        LiveMode::DataHub => NewLiveOverlay::DataHub(NewDataHub::default()),
    }) {
        NewLiveOverlay::None => LiveOverlay::None,
        NewLiveOverlay::Rtsp { handler: entered } => LiveOverlay::Rtsp {
            handler: text(&entered, &defaults.rtsp_handler),
        },
        NewLiveOverlay::Mqtt(mqtt) => {
            let broker = &defaults.mqtt;
            LiveOverlay::Mqtt {
                handler: text(&mqtt.handler, &broker.handler),
                broker_host: text(&mqtt.broker_host, &broker.host),
                broker_port: narrow(
                    mqtt.broker_port.unwrap_or_else(|| broker.port.into()),
                    "broker_port",
                    "MQTT Port must be between 1 and 65535.",
                )?,
                broker_user: text(&mqtt.broker_user, &broker.user),
                broker_pass: secret(&mqtt.broker_pass, &broker.password),
                source_key: text(&mqtt.source_key, &broker.source_key),
            }
        }
        NewLiveOverlay::DataHub(hub) => {
            let datahub = &defaults.datahub;
            LiveOverlay::DataHub {
                handler: text(&hub.handler, &datahub.handler),
                topic: text(&hub.topic, &datahub.topic),
                channel: narrow(
                    hub.channel.unwrap_or_else(|| datahub.channel.into()),
                    "channel",
                    "Channel ID must be at most 4294967295.",
                )?,
            }
        }
    };
    let recipe = LiveRecipe {
        host: text(&new.host, &defaults.host),
        username: text(&new.username, &defaults.username),
        password: secret(&new.password, &defaults.password),
        protocol,
        port,
        head,
        rtsp_port,
        resolution: Some(text(&new.resolution, &defaults.resolution)).filter(|r| !r.is_empty()),
        url: Some(text(&new.url, "")).filter(|url| !url.is_empty()),
        overlay,
    };
    recipe.check(handlers)?;
    Ok(WorkspaceItem {
        id: ItemId::generate(),
        label: new
            .label
            .as_deref()
            .map(str::trim)
            .unwrap_or_default()
            .to_owned(),
        recipe: Recipe::new(Source::Live(recipe)),
    })
}
