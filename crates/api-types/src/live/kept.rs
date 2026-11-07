//! What the overlay modes not chosen hold, as a request carries it: no decoder, since nothing reads it until its mode
//! is chosen.

use super::{NewLiveWire, WireSecret, reference_only};
use serde::{Deserialize, Serialize};
use ts_rs::TS;
use workspace::{DataHubSettings, LiveRecipe, MqttSettings, Secret, intake};

/// The MQTT settings of a stream whose overlay is another mode. Mirrors `workspace::MqttSettings`; what is absent takes
/// the configured default.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct KeptMqttWire {
    /// The broker host.
    pub broker_host: Option<String>,
    /// The broker port.
    #[ts(type = "number", optional)]
    pub broker_port: Option<i64>,
    /// The broker user.
    pub broker_user: Option<String>,
    /// The broker password.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub broker_pass: Option<WireSecret>,
    /// The camera's analytics data source.
    pub source_key: Option<String>,
}

/// The DataHub settings of a stream whose overlay is another mode. Mirrors `workspace::DataHubSettings`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(default)]
#[ts(optional_fields)]
pub struct KeptDataHubWire {
    /// The topic.
    pub topic: Option<String>,
    /// The topic's instance.
    #[ts(type = "number", optional)]
    pub channel: Option<i64>,
}

impl From<&MqttSettings> for KeptMqttWire {
    fn from(mqtt: &MqttSettings) -> Self {
        Self {
            broker_host: Some(mqtt.broker_host.clone()),
            broker_port: Some(mqtt.broker_port),
            broker_user: Some(mqtt.broker_user.clone()),
            broker_pass: reference_only(&mqtt.broker_pass),
            source_key: Some(mqtt.source_key.clone()),
        }
    }
}

impl From<&DataHubSettings> for KeptDataHubWire {
    fn from(hub: &DataHubSettings) -> Self {
        Self {
            topic: Some(hub.topic.clone()),
            channel: Some(hub.channel),
        }
    }
}

impl From<KeptMqttWire> for intake::NewMqtt {
    fn from(mqtt: KeptMqttWire) -> Self {
        Self {
            handler: None,
            broker_host: mqtt.broker_host,
            broker_port: mqtt.broker_port,
            broker_user: mqtt.broker_user,
            broker_pass: mqtt.broker_pass.map(Secret::from),
            source_key: mqtt.source_key,
        }
    }
}

impl NewLiveWire {
    /// For an edit of the stream `saved`: what the request leaves out (the overlay, the settings of the modes not
    /// chosen) is what `saved` holds. A literal password is not sent, as in any recipe request; the intake restores it.
    pub fn keep_unsent(&mut self, saved: &LiveRecipe) {
        if self.overlay.is_none() {
            self.overlay = Some((&saved.overlay).into());
        }
        if self.kept_mqtt.is_none() {
            self.kept_mqtt = saved.kept.mqtt.as_ref().map(Into::into);
        }
        if self.kept_datahub.is_none() {
            self.kept_datahub = saved.kept.datahub.as_ref().map(Into::into);
        }
    }
}

impl From<KeptDataHubWire> for intake::NewDataHub {
    fn from(hub: KeptDataHubWire) -> Self {
        Self {
            handler: None,
            topic: hub.topic,
            channel: hub.channel,
        }
    }
}
