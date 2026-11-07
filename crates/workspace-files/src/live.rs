//! The `live` item of the workspace file: a camera's connection settings, with passwords and `$NAME` references as
//! entered.

use foundation::ItemId;
use overlay_sync::MatchPolicy;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use workspace::{
    DataHubSettings, KeptTransports, LiveOverlay, LiveRecipe, MqttSettings, Protocol, Secret,
    Source,
};

/// A live stream, with passwords and references as entered.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LiveItem {
    kind: String,
    id: ItemId,
    label: String,
    host: String,
    #[serde(default)]
    username: String,
    #[serde(default)]
    password: String,
    protocol: ProtocolDto,
    #[serde(default)]
    port: u16,
    head: u16,
    rtsp_port: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    resolution: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    url: Option<String>,
    overlay: LiveOverlayDto,
    /// What the overlay modes not chosen hold; absent in files from before it was kept.
    #[serde(default, skip_serializing_if = "KeptDto::is_empty")]
    kept: KeptDto,
    #[serde(default)]
    lane_matching: BTreeMap<String, MatchPolicy>,
}

#[derive(Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct KeptDto {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    mqtt: Option<MqttDto>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    datahub: Option<DataHubDto>,
}

impl KeptDto {
    fn is_empty(&self) -> bool {
        self.mqtt.is_none() && self.datahub.is_none()
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MqttDto {
    broker_host: String,
    broker_port: i64,
    #[serde(default)]
    broker_user: String,
    #[serde(default)]
    broker_pass: String,
    source_key: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DataHubDto {
    topic: String,
    channel: i64,
}

impl From<&KeptTransports> for KeptDto {
    fn from(kept: &KeptTransports) -> Self {
        Self {
            mqtt: kept.mqtt.as_ref().map(|mqtt| MqttDto {
                broker_host: mqtt.broker_host.clone(),
                broker_port: mqtt.broker_port,
                broker_user: mqtt.broker_user.clone(),
                broker_pass: mqtt.broker_pass.expose().to_owned(),
                source_key: mqtt.source_key.clone(),
            }),
            datahub: kept.datahub.as_ref().map(|hub| DataHubDto {
                topic: hub.topic.clone(),
                channel: hub.channel,
            }),
        }
    }
}

impl From<KeptDto> for KeptTransports {
    fn from(kept: KeptDto) -> Self {
        Self {
            mqtt: kept.mqtt.map(|mqtt| MqttSettings {
                broker_host: mqtt.broker_host,
                broker_port: mqtt.broker_port,
                broker_user: mqtt.broker_user,
                broker_pass: Secret::new(mqtt.broker_pass),
                source_key: mqtt.source_key,
            }),
            datahub: kept.datahub.map(|hub| DataHubSettings {
                topic: hub.topic,
                channel: hub.channel,
            }),
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Copy)]
#[serde(rename_all = "snake_case")]
enum ProtocolDto {
    Http,
    Https,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
enum LiveOverlayDto {
    None,
    Rtsp {
        handler: String,
    },
    Mqtt {
        handler: String,
        broker_host: String,
        broker_port: u16,
        #[serde(default)]
        broker_user: String,
        #[serde(default)]
        broker_pass: String,
        source_key: String,
    },
    Datahub {
        handler: String,
        topic: String,
        channel: u32,
    },
}

impl From<&LiveOverlay> for LiveOverlayDto {
    fn from(overlay: &LiveOverlay) -> Self {
        match overlay {
            LiveOverlay::None => Self::None,
            LiveOverlay::Rtsp { handler } => Self::Rtsp {
                handler: handler.clone(),
            },
            LiveOverlay::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt {
                handler: handler.clone(),
                broker_host: broker_host.clone(),
                broker_port: *broker_port,
                broker_user: broker_user.clone(),
                broker_pass: broker_pass.expose().to_owned(),
                source_key: source_key.clone(),
            },
            LiveOverlay::DataHub {
                handler,
                topic,
                channel,
            } => Self::Datahub {
                handler: handler.clone(),
                topic: topic.clone(),
                channel: *channel,
            },
        }
    }
}

impl From<LiveOverlayDto> for LiveOverlay {
    fn from(overlay: LiveOverlayDto) -> Self {
        match overlay {
            LiveOverlayDto::None => Self::None,
            LiveOverlayDto::Rtsp { handler } => Self::Rtsp { handler },
            LiveOverlayDto::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass,
                source_key,
            } => Self::Mqtt {
                handler,
                broker_host,
                broker_port,
                broker_user,
                broker_pass: Secret::new(broker_pass),
                source_key,
            },
            LiveOverlayDto::Datahub {
                handler,
                topic,
                channel,
            } => Self::DataHub {
                handler,
                topic,
                channel,
            },
        }
    }
}

impl LiveItem {
    /// The file's form of the live item `id`.
    pub(crate) fn new(
        id: ItemId,
        label: String,
        recipe: &LiveRecipe,
        lane_matching: BTreeMap<String, MatchPolicy>,
    ) -> Self {
        Self {
            kind: "live".into(),
            id,
            label,
            host: recipe.host.clone(),
            username: recipe.username.clone(),
            password: recipe.password.expose().to_owned(),
            protocol: match recipe.protocol {
                Protocol::Http => ProtocolDto::Http,
                Protocol::Https => ProtocolDto::Https,
            },
            port: recipe.port,
            head: recipe.head,
            rtsp_port: recipe.rtsp_port,
            resolution: recipe.resolution.clone(),
            url: recipe.url.clone(),
            overlay: (&recipe.overlay).into(),
            kept: (&recipe.kept).into(),
            lane_matching,
        }
    }

    /// The item's id, label, source and lane matching.
    pub(crate) fn into_parts(self) -> (ItemId, String, Source, BTreeMap<String, MatchPolicy>) {
        let source = Source::Live(LiveRecipe {
            host: self.host,
            username: self.username,
            password: Secret::new(self.password),
            protocol: match self.protocol {
                ProtocolDto::Http => Protocol::Http,
                ProtocolDto::Https => Protocol::Https,
            },
            port: self.port,
            head: self.head,
            rtsp_port: self.rtsp_port,
            resolution: self.resolution,
            url: self.url,
            overlay: self.overlay.into(),
            kept: self.kept.into(),
        });
        (self.id, self.label, source, self.lane_matching)
    }
}
