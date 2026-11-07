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

/// The text of `key` as it is used when `expands`: a `$NAME` reference is its variable's value, copied into the new
/// item, unlike a connection text, which stays a reference. Without `expands` it is the text as written.
fn value(state: &State, key: &str, expands: bool) -> String {
    let value = if expands {
        state.resolved(key)
    } else {
        state.written(key)
    };
    value
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default()
}

/// The number of `key`: what a reference gives when `expands`, else the setting's default for a reference.
fn whole<T: TryFrom<u64> + Default>(state: &State, key: &str, expands: bool) -> T {
    let value = if expands {
        state.stored(key).cloned()
    } else {
        state.written(key)
    };
    value
        .and_then(|value| value.as_u64())
        .and_then(|number| T::try_from(number).ok())
        .unwrap_or_default()
}

/// The values a new Live Stream Item takes for what the user left empty, for a caller whose references `expands`. The
/// host, login and broker texts are as written
/// in the settings (a `$NAME` reference stays one); the other values are copied as they are used.
pub fn defaults(state: &State, expands: bool) -> LiveDefaults {
    LiveDefaults {
        host: text(state, "camera.host"),
        username: text(state, "camera.user"),
        password: Secret::new(text(state, "camera.password")),
        protocol: match text(state, "camera.protocol").as_str() {
            "http" => Protocol::Http,
            _ => Protocol::Https,
        },
        head: whole(state, "camera.head", expands),
        rtsp_port: whole(state, "camera.rtsp_port", expands),
        resolution: text(state, "camera.resolution"),
        overlay: match value(state, "camera.overlay", expands).as_str() {
            "none" => LiveMode::None,
            "rtsp" => LiveMode::Rtsp,
            "mqtt" => LiveMode::Mqtt,
            _ => LiveMode::DataHub,
        },
        rtsp_handler: value(state, "camera.rtsp_handler", expands),
        mqtt: MqttDefaults {
            handler: value(state, "broker.handler", expands),
            host: text(state, "broker.host"),
            user: text(state, "broker.user"),
            password: Secret::new(text(state, "broker.password")),
            port: whole(state, "broker.port", expands),
            source_key: value(state, "broker.source_key", expands),
        },
        datahub: DataHubDefaults {
            handler: value(state, "datahub.handler", expands),
            topic: value(state, "datahub.topic", expands),
            channel: whole(state, "datahub.channel", expands),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::defaults;
    use serde_json::json;

    /// `PATH` stands for any variable: a reference in a default is copied as its value for the person at this machine
    /// and stays the text it is for a remote viewer, a number reference giving its default.
    #[test]
    fn a_reference_in_a_default_is_copied_only_for_a_caller_who_expands() {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        settings
            .update("datahub.topic", |_| Some(json!("$PATH")))
            .unwrap();
        let state = settings.state();
        assert_eq!(
            defaults(&state, true).datahub.topic,
            std::env::var("PATH").unwrap()
        );
        assert_eq!(defaults(&state, false).datahub.topic, "$PATH");
    }
}
