//! The request to add or edit a live stream, and the password it may carry.

use super::{KeptDataHubWire, KeptMqttWire};
use foundation::ItemId;
use serde::{Deserialize, Serialize};
use std::fmt;
use ts_rs::TS;
use workspace::{LiveOverlay, LiveRecipe, Protocol, RtspRoute, Secret, StreamUrl, intake};

/// A password in a request. `Debug` prints `***`, so a logged request cannot leak it.
#[derive(Clone, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[ts(type = "string")]
pub struct WireSecret(String);

impl fmt::Debug for WireSecret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("***")
    }
}

impl From<WireSecret> for Secret {
    fn from(secret: WireSecret) -> Self {
        Self::new(secret.0)
    }
}

/// The overlay the user chose for a new stream; anything left out takes the configured default. Mirrors
/// `workspace::intake::NewLiveOverlay`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "mode", rename_all = "lowercase")]
#[ts(optional_fields)]
pub enum NewLiveOverlayWire {
    /// Video only.
    None,
    /// Metadata inside the RTSP stream.
    Rtsp {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
    },
    /// An MQTT broker.
    Mqtt {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
        /// The broker host.
        #[serde(default)]
        broker_host: Option<String>,
        /// The broker port, checked against 1 to 65535.
        #[serde(default)]
        #[ts(type = "number", optional)]
        broker_port: Option<i64>,
        /// The broker user.
        #[serde(default)]
        broker_user: Option<String>,
        /// The broker password.
        #[serde(default)]
        #[serde(skip_serializing_if = "Option::is_none")]
        broker_pass: Option<WireSecret>,
        /// The camera's analytics data source.
        #[serde(default)]
        source_key: Option<String>,
    },
    /// A DataHub topic.
    DataHub {
        /// The decoder id.
        #[serde(default)]
        handler: Option<String>,
        /// The topic.
        #[serde(default)]
        topic: Option<String>,
        /// The topic's instance, from 1.
        #[serde(default)]
        #[ts(type = "number", optional)]
        channel: Option<i64>,
    },
}

/// What the user entered to add a live stream. Text that is absent or blank takes its configured default; numbers are
/// checked against their range by the intake, so its message names the field. Mirrors `workspace::intake::NewLive`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct NewLiveWire {
    /// The name; the host when empty.
    pub label: Option<String>,
    /// The camera host or a `$NAME` reference.
    pub host: Option<String>,
    /// The camera user or a reference.
    pub username: Option<String>,
    /// The camera password or a reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub password: Option<WireSecret>,
    /// The web interface's protocol.
    pub protocol: Option<Protocol>,
    /// The web interface's port, 0 to 65535; 0 or absent is the standard one.
    #[ts(type = "number", optional)]
    pub port: Option<i64>,
    /// The video channel, from 1.
    #[ts(type = "number", optional)]
    pub head: Option<i64>,
    /// The RTSP port, 1 to 65535.
    #[ts(type = "number", optional)]
    pub rtsp_port: Option<i64>,
    /// Which door of the camera the stream comes through; absent is the configured one.
    pub rtsp_route: Option<RtspRoute>,
    /// The resolution such as `1280x720`; blank is the camera's own.
    pub resolution: Option<String>,
    /// A stream URL to use instead of the one made from the host, as typed. A saved URL is sent without its login's
    /// password, and one sent back that way keeps the saved password.
    #[ts(type = "string", optional)]
    pub url: Option<StreamUrl>,
    /// The overlay; absent is the configured one for a new stream and the saved one for an edit.
    pub overlay: Option<NewLiveOverlayWire>,
    /// What the form holds for MQTT while another overlay mode is chosen, kept with the stream.
    pub kept_mqtt: Option<KeptMqttWire>,
    /// What the form holds for DataHub while another overlay mode is chosen, kept with the stream.
    pub kept_datahub: Option<KeptDataHubWire>,
    /// The address of a camera the network scan signed in to. The backend then takes the host, protocol, port and
    /// login from the scan, so the login never reaches the client, and the fields above fill in the rest. Only the
    /// session's owner may add such a stream. Never part of a stored recipe.
    pub found: Option<String>,
    /// A live stream this one is another of. The backend fills a blank user and password from that stream's saved
    /// login, so a literal password never reaches the client, and only while the host, protocol and port are the
    /// same as that stream's and the seat is the session's owner. Never part of a stored recipe.
    #[ts(type = "string", optional)]
    pub like: Option<ItemId>,
}

impl From<NewLiveOverlayWire> for intake::NewLiveOverlay {
    fn from(overlay: NewLiveOverlayWire) -> Self {
        match overlay {
            NewLiveOverlayWire::None => Self::None,
            NewLiveOverlayWire::Rtsp { handler } => Self::Rtsp { handler },
            NewLiveOverlayWire::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt(intake::NewMqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass: broker_pass.map(Secret::from),
                source_key,
            }),
            NewLiveOverlayWire::DataHub {
                handler,
                topic,
                channel,
            } => Self::DataHub(intake::NewDataHub {
                handler,
                topic,
                channel,
            }),
        }
    }
}

/// A stored password as a request may carry it: a `$NAME` reference as it is, an empty one as empty (so a form can tell
/// it from a literal), and nothing for a literal secret, which never leaves the host.
pub(super) fn reference_only(secret: &Secret) -> Option<WireSecret> {
    if secret.expose().is_empty() {
        return Some(WireSecret::default());
    }
    secret
        .as_reference()
        .map(|reference| WireSecret(reference.to_owned()))
}

impl From<&LiveOverlay> for NewLiveOverlayWire {
    fn from(overlay: &LiveOverlay) -> Self {
        match overlay {
            LiveOverlay::None => Self::None,
            LiveOverlay::Rtsp { handler } => Self::Rtsp {
                handler: Some(handler.clone()),
            },
            LiveOverlay::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt {
                handler: Some(handler.clone()),
                broker_host: Some(broker_host.clone()),
                broker_port: Some(i64::from(*broker_port)),
                broker_user: Some(broker_user.clone()),
                broker_pass: reference_only(broker_pass),
                source_key: Some(source_key.clone()),
            },
            LiveOverlay::DataHub {
                handler,
                topic,
                channel,
            } => Self::DataHub {
                handler: Some(handler.clone()),
                topic: Some(topic.clone()),
                channel: Some(i64::from(*channel)),
            },
        }
    }
}

/// The request that would make `recipe` again, for a client that may know the host. Text is as stored, so a `$NAME`
/// reference stays one; a literal password is left out. The label is not part of a recipe and stays unset.
impl From<&LiveRecipe> for NewLiveWire {
    fn from(recipe: &LiveRecipe) -> Self {
        let LiveRecipe {
            host,
            username,
            password,
            protocol,
            port,
            head,
            rtsp_port,
            rtsp_route,
            resolution,
            url,
            overlay,
            kept,
        } = recipe;
        Self {
            label: None,
            host: Some(host.clone()),
            username: Some(username.clone()),
            password: reference_only(password),
            protocol: Some(*protocol),
            port: Some(i64::from(*port)),
            head: Some(i64::from(*head)),
            rtsp_port: Some(i64::from(*rtsp_port)),
            rtsp_route: Some(*rtsp_route),
            resolution: resolution.clone(),
            url: url.as_ref().map(StreamUrl::without_password),
            overlay: Some(overlay.into()),
            kept_mqtt: kept.mqtt.as_ref().map(Into::into),
            kept_datahub: kept.datahub.as_ref().map(Into::into),
            found: None,
            like: None,
        }
    }
}

impl From<NewLiveWire> for intake::NewLive {
    fn from(new: NewLiveWire) -> Self {
        Self {
            label: new.label,
            host: new.host,
            username: new.username,
            password: new.password.map(Secret::from),
            protocol: new.protocol,
            port: new.port,
            head: new.head,
            rtsp_port: new.rtsp_port,
            rtsp_route: new.rtsp_route,
            resolution: new.resolution,
            url: new.url,
            overlay: new.overlay.map(Into::into),
            kept_mqtt: new.kept_mqtt.map(Into::into),
            kept_datahub: new.kept_datahub.map(Into::into),
        }
    }
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<WireSecret>();
    types.visit::<NewLiveOverlayWire>();
    types.visit::<NewLiveWire>();
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Spellings of one URL whose password a plain text search of the URL would not find.
    const SPELLINGS: [&str; 5] = [
        "http://root:SECRET@cam/s",
        "http:root:SECRET@cam/s",
        "http:////root:SECRET@cam/s",
        "HTTP://root:SECRET@CAM:80/s",
        "rtsp://root:SE%43RET@cam/s",
    ];

    fn recipe(url: &str) -> LiveRecipe {
        LiveRecipe {
            host: "cam".into(),
            username: String::new(),
            password: Secret::new(""),
            protocol: Protocol::Https,
            port: 0,
            head: 1,
            rtsp_port: 554,
            rtsp_route: RtspRoute::Direct,
            resolution: None,
            url: Some(url.into()),
            overlay: LiveOverlay::None,
            kept: Default::default(),
        }
    }

    #[test]
    fn a_hostless_or_opaque_url_goes_to_a_client_as_a_placeholder() {
        for url in [
            "rtsp:////root:SECRET@cam/s",
            "rtsp:root:SECRET@cam",
            "rtsp://:SECRET@/s",
        ] {
            let wire = NewLiveWire::from(&recipe(url));
            assert_eq!(wire.url.as_deref(), Some("(unusable URL)"), "{url}");
            assert!(!format!("{wire:?}{wire:#?}").contains("SECRET"), "{url}");
        }
    }

    #[test]
    fn the_request_a_recipe_makes_again_carries_its_urls_user_but_never_its_password() {
        for url in SPELLINGS {
            let wire = NewLiveWire::from(&recipe(url));
            let sent = wire.url.as_deref().unwrap();
            assert!(sent.contains("root@cam"), "{url} -> {sent}");
            for text in [
                serde_json::to_string(&wire).unwrap(),
                format!("{wire:?}"),
                format!("{wire:#?}"),
            ] {
                assert!(!text.to_uppercase().contains("SECRET"), "{url}: {text}");
            }
        }
    }
}
