//! Intake of a Live Stream Item: what the user entered, with the configured defaults for what they left empty.

use super::live_defaults::{DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults};
use crate::{
    credentials::{Secret, reference},
    item::{Recipe, Source, WorkspaceItem},
    live::{
        DataHubSettings, KeptTransports, LiveOverlay, LiveRecipe, MqttSettings, Protocol,
        url_endpoint,
    },
};
use foundation::{ErrorCode, ItemId, Location, UserError};
use handlers::Handlers;

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
    /// What the form holds for MQTT while another mode is chosen; its handler is not used. Absent parts, and all of it
    /// when absent, take the defaults.
    pub kept_mqtt: Option<NewMqtt>,
    /// What the form holds for DataHub while another mode is chosen; its handler is not used.
    pub kept_datahub: Option<NewDataHub>,
}

/// What a copied login is for, which decides where the copy may send it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoginUse {
    /// A new stream, which sends the login to its RTSP endpoint as well as to the web interface.
    Stream,
    /// Questions to the camera's web interface only, such as stream sizes and analytics sources.
    Discovery,
}

impl NewLive {
    /// Fills a blank user and password from the login saved with `source`, when this stream is for the same camera:
    /// the same host text, protocol and port once `defaults` fill what is blank. Another host gets nothing, so a saved
    /// password is never sent where the person who adds the stream did not first see it go. A user entered and not
    /// `source`'s keeps its own password too, which `source`'s does not belong to, and a user cleared to empty gets
    /// none of `source`'s.
    ///
    /// A [`LoginUse::Stream`] login also goes to the stream URL's host and port, or the camera's with its RTSP port,
    /// so a copy needs that endpoint to be the one `source` already sends its login to; any other is refused, as the
    /// person must then enter the login for that server. A [`LoginUse::Discovery`] login goes only to the web
    /// interface the camera check above compares, so it has no further destination to check.
    ///
    /// An MQTT broker password left blank is filled from `source`'s too, when the broker host, port and user are
    /// the same.
    pub fn copy_login(
        &mut self,
        source: &LiveRecipe,
        defaults: &LiveDefaults,
        usage: LoginUse,
    ) -> Result<(), UserError> {
        self.copy_broker_password(source, defaults);
        let protocol = self.protocol.unwrap_or(defaults.protocol);
        let port = |protocol: Protocol, port: i64| match port {
            0 => i64::from(protocol.default_port()),
            port => port,
        };
        let same_camera = same_host(&text(&self.host, &defaults.host), source.host.trim())
            && protocol == source.protocol
            && port(protocol, self.port.unwrap_or(0)) == port(source.protocol, source.port.into());
        if !same_camera {
            return Ok(());
        }
        let user_blank = match self.username.as_deref() {
            Some(user) if !user.is_empty() && user != source.username => return Ok(()),
            Some(user) => user.is_empty(),
            None => true,
        };
        let password_blank = self
            .password
            .as_ref()
            .is_none_or(|entered| entered.expose().is_empty());
        if !user_blank && !password_blank {
            return Ok(());
        }
        if usage == LoginUse::Stream {
            self.check_login_destination(&source.rtsp_endpoint(), defaults)?;
        }
        // A user entered as empty (an edit that cleared it) stays empty; only one not given is the saved one.
        if self.username.is_none() {
            self.username = Some(source.username.clone());
        }
        // Only a literal is copied, and only to the user it was saved for: the client was sent a reference, and a
        // blank one came back cleared on purpose.
        if password_blank
            && self.username.as_deref() == Some(source.username.as_str())
            && source.password.as_reference().is_none()
        {
            self.password = Some(source.password.clone());
        }
        Ok(())
    }

    /// Fills a blank MQTT broker password, in the overlay and in the kept settings, from `source`'s MQTT settings when
    /// this stream's broker is the same one, with the same user, once `defaults` fill what is blank: the password goes
    /// to the broker it was saved for and no other.
    fn copy_broker_password(&mut self, source: &LiveRecipe, defaults: &LiveDefaults) {
        let Some(saved) = source.mqtt_settings() else {
            return;
        };
        let broker = &defaults.mqtt;
        let fill = |mqtt: &mut NewMqtt| {
            let blank = mqtt
                .broker_pass
                .as_ref()
                .is_none_or(|entered| entered.expose().is_empty());
            let port = mqtt.broker_port.unwrap_or_else(|| broker.port.into());
            if blank
                && saved.broker_pass.as_reference().is_none()
                && same_host(
                    &text(&mqtt.broker_host, &broker.host),
                    saved.broker_host.trim(),
                )
                && port == saved.broker_port
                && login(&mqtt.broker_user, &broker.user) == saved.broker_user
            {
                mqtt.broker_pass = Some(saved.broker_pass.clone());
            }
        };
        if let Some(NewLiveOverlay::Mqtt(mqtt)) = &mut self.overlay {
            fill(mqtt);
        }
        if let Some(mqtt) = &mut self.kept_mqtt {
            fill(mqtt);
        }
    }

    /// Refuses, with the field `password`, when the host and port this stream's RTSP goes to, once `defaults` fill
    /// what is blank, differ from `trusted`: the one endpoint a saved or scanned login may be sent to.
    pub fn check_login_destination(
        &self,
        trusted: &(String, u16),
        defaults: &LiveDefaults,
    ) -> Result<(), UserError> {
        let endpoint = match self.url.as_deref().map(str::trim) {
            Some(url) if !url.is_empty() => url_endpoint(url).ok(),
            _ => Some(self.camera_endpoint(defaults)),
        };
        if endpoint.as_ref().map(canonical_endpoint) == Some(canonical_endpoint(trusted)) {
            return Ok(());
        }
        Err(invalid(
            "password",
            "A saved login is not sent to another RTSP address; enter the user and password for it.",
        ))
    }

    /// The host and port the camera itself takes RTSP on: the endpoint a scan's login may be sent to.
    pub fn camera_endpoint(&self, defaults: &LiveDefaults) -> (String, u16) {
        let rtsp_port = self.rtsp_port.unwrap_or_else(|| defaults.rtsp_port.into());
        (
            text(&self.host, &defaults.host).to_ascii_lowercase(),
            u16::try_from(rtsp_port).unwrap_or(0),
        )
    }
}

/// Whether two host texts are the same camera: a literal name ignores case, but an environment reference names a
/// variable, and `$CAM` and `$cam` are two of them.
fn same_host(a: &str, b: &str) -> bool {
    if reference(a).is_some() || reference(b).is_some() {
        a == b
    } else {
        a.eq_ignore_ascii_case(b)
    }
}

/// An endpoint with its host spelled one way: no IPv6 brackets, an address in its canonical form, a name lowercase.
fn canonical_endpoint((host, port): &(String, u16)) -> (String, u16) {
    let bare = host.trim_start_matches('[').trim_end_matches(']');
    let host = match bare.parse::<std::net::IpAddr>() {
        Ok(address) => address.to_string(),
        Err(_) => bare.to_ascii_lowercase(),
    };
    (host, *port)
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

/// The `entered` user exactly as typed, spaces included, or `default` when it is none or empty.
fn login(entered: &Option<String>, default: &str) -> String {
    match entered.as_deref() {
        Some(entered) if !entered.is_empty() => entered.to_owned(),
        _ => default.to_owned(),
    }
}

/// The `entered` password exactly as typed, spaces included, or `default` when it is none or empty.
fn secret(entered: &Option<Secret>, default: &Secret) -> Secret {
    match entered {
        Some(entered) if !entered.expose().is_empty() => entered.clone(),
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

/// The MQTT settings `entered` gives, empty parts taking `defaults`. The port is as entered: a mode not chosen is
/// not range-checked, and choosing it later checks it.
fn mqtt_settings(entered: &NewMqtt, defaults: &MqttDefaults) -> MqttSettings {
    MqttSettings {
        broker_host: text(&entered.broker_host, &defaults.host),
        broker_port: entered.broker_port.unwrap_or_else(|| defaults.port.into()),
        broker_user: login(&entered.broker_user, &defaults.user),
        broker_pass: secret(&entered.broker_pass, &defaults.password),
        source_key: text(&entered.source_key, &defaults.source_key),
    }
}

/// The DataHub settings `entered` gives, empty parts taking `defaults`. The channel is as entered, like the port above.
fn datahub_settings(entered: &NewDataHub, defaults: &DataHubDefaults) -> DataHubSettings {
    DataHubSettings {
        topic: text(&entered.topic, &defaults.topic),
        channel: entered.channel.unwrap_or_else(|| defaults.channel.into()),
    }
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
    let head = narrow::<u16>(
        new.head.unwrap_or_else(|| defaults.head.into()),
        "head",
        "Camera Head must be at most 9999.",
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
            let broker = mqtt_settings(&mqtt, &defaults.mqtt);
            LiveOverlay::Mqtt {
                handler: text(&mqtt.handler, &defaults.mqtt.handler),
                broker_host: broker.broker_host,
                broker_port: narrow(
                    broker.broker_port,
                    "broker_port",
                    "MQTT Port must be between 1 and 65535.",
                )?,
                broker_user: broker.broker_user,
                broker_pass: broker.broker_pass,
                source_key: broker.source_key,
            }
        }
        NewLiveOverlay::DataHub(hub) => {
            let datahub = datahub_settings(&hub, &defaults.datahub);
            LiveOverlay::DataHub {
                handler: text(&hub.handler, &defaults.datahub.handler),
                topic: datahub.topic,
                channel: narrow(
                    datahub.channel,
                    "channel",
                    "Channel ID must be at most 4294967295.",
                )?,
            }
        }
    };
    // The modes not chosen keep what was entered for them, or else the defaults as written, except the default
    // broker password: it is never copied into a stream that does not use the broker.
    let no_secret = MqttDefaults {
        password: Secret::new(""),
        ..defaults.mqtt.clone()
    };
    let kept = KeptTransports {
        mqtt: match overlay {
            LiveOverlay::Mqtt { .. } => None,
            _ => Some(mqtt_settings(
                &new.kept_mqtt.clone().unwrap_or_default(),
                &no_secret,
            )),
        },
        datahub: match overlay {
            LiveOverlay::DataHub { .. } => None,
            _ => Some(datahub_settings(
                &new.kept_datahub.clone().unwrap_or_default(),
                &defaults.datahub,
            )),
        },
    };
    let recipe = LiveRecipe {
        host: text(&new.host, &defaults.host),
        username: login(&new.username, &defaults.username),
        password: secret(&new.password, &defaults.password),
        protocol,
        port,
        head,
        rtsp_port,
        resolution: Some(text(&new.resolution, "")).filter(|r| !r.is_empty()),
        url: Some(text(&new.url, "")).filter(|url| !url.is_empty()),
        overlay,
        kept,
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
