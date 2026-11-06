//! What the live settings give a Live Stream Item: the defaults a new one starts from.

use settings::State;
use workspace::{
    Protocol, Secret,
    intake::{DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults},
};

fn text(state: &State, key: &str) -> String {
    state
        .stored(key)
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_owned()
}

/// The text of `key` as it is used: a `$NAME` reference is its variable's value, copied into the new item, unlike a
/// connection text, which stays a reference.
fn value(state: &State, key: &str) -> String {
    state
        .resolved(key)
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default()
}

fn whole<T: TryFrom<u64> + Default>(state: &State, key: &str) -> T {
    state
        .stored(key)
        .and_then(|value| value.as_u64())
        .and_then(|number| T::try_from(number).ok())
        .unwrap_or_default()
}

/// The values a new Live Stream Item takes for what the user left empty. The host, login and broker texts are as written
/// in the settings (a `$NAME` reference stays one); the other values are copied as they are used.
pub fn defaults(state: &State) -> LiveDefaults {
    LiveDefaults {
        host: text(state, "camera.host"),
        username: text(state, "camera.user"),
        password: Secret::new(text(state, "camera.password")),
        protocol: match text(state, "camera.protocol").as_str() {
            "http" => Protocol::Http,
            _ => Protocol::Https,
        },
        head: whole(state, "camera.head"),
        rtsp_port: whole(state, "camera.rtsp_port"),
        resolution: text(state, "camera.resolution"),
        overlay: match value(state, "camera.overlay").as_str() {
            "none" => LiveMode::None,
            "rtsp" => LiveMode::Rtsp,
            "mqtt" => LiveMode::Mqtt,
            _ => LiveMode::DataHub,
        },
        rtsp_handler: value(state, "camera.rtsp_handler"),
        mqtt: MqttDefaults {
            handler: value(state, "broker.handler"),
            host: text(state, "broker.host"),
            user: text(state, "broker.user"),
            password: Secret::new(text(state, "broker.password")),
            port: whole(state, "broker.port"),
            source_key: value(state, "broker.source_key"),
        },
        datahub: DataHubDefaults {
            handler: value(state, "datahub.handler"),
            topic: value(state, "datahub.topic"),
            channel: whole(state, "datahub.channel"),
        },
    }
}
