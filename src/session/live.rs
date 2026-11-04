//! What the live settings give a Live Stream Item: the defaults a new one starts from, and the environment its
//! `$NAME` references expand in when it resolves.

use settings::State;
use workspace::{
    Confirmation, Environment, Protocol, Secret,
    intake::{DataHubDefaults, LiveDefaults, LiveMode, MqttDefaults},
};

fn text(state: &State, key: &str) -> String {
    state
        .stored(key)
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_owned()
}

fn whole<T: TryFrom<u64> + Default>(state: &State, key: &str) -> T {
    state
        .stored(key)
        .and_then(|value| value.as_u64())
        .and_then(|number| T::try_from(number).ok())
        .unwrap_or_default()
}

/// The values a new Live Stream Item takes for what the user left empty, as written in the settings: a `$NAME`
/// reference stays one.
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
        overlay: match text(state, "camera.overlay").as_str() {
            "none" => LiveMode::None,
            "rtsp" => LiveMode::Rtsp,
            "mqtt" => LiveMode::Mqtt,
            _ => LiveMode::DataHub,
        },
        rtsp_handler: text(state, "camera.rtsp_handler"),
        mqtt: MqttDefaults {
            handler: text(state, "broker.handler"),
            host: text(state, "broker.host"),
            user: text(state, "broker.user"),
            password: Secret::new(text(state, "broker.password")),
            port: whole(state, "broker.port"),
            source_key: text(state, "broker.source_key"),
        },
        datahub: DataHubDefaults {
            handler: text(state, "datahub.handler"),
            topic: text(state, "datahub.topic"),
            channel: whole(state, "datahub.channel"),
        },
    }
}

/// The process's variables, with the references the user confirmed in `camera.confirmed` allowed.
pub(crate) fn environment(state: &State) -> Environment {
    let confirmed = state
        .stored("camera.confirmed")
        .and_then(|value| value.as_array())
        .into_iter()
        .flatten()
        .filter_map(|record| {
            let field = |name| record.get(name)?.as_str().map(str::to_owned);
            Some(Confirmation {
                reference: field("reference")?,
                host: field("host")?,
            })
        });
    Environment::new(|name| std::env::var(name).ok(), confirmed)
}

/// Remembers that the user lets `confirmation.reference` be sent to `confirmation.host`, as `camera.confirmed` keeps it.
/// Saying it twice changes nothing.
pub fn confirm(
    settings: &settings::Settings,
    confirmation: &Confirmation,
) -> Result<(), settings::Error> {
    let record =
        serde_json::json!({ "reference": confirmation.reference, "host": confirmation.host });
    // One read and write under the settings' lock, so a concurrent confirmation or save is never overwritten.
    settings.update("camera.confirmed", |stored| {
        let mut list = stored.as_array().cloned().unwrap_or_default();
        if list.contains(&record) {
            return None;
        }
        list.push(record);
        Some(serde_json::Value::Array(list))
    })?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confirmations_from_concurrent_windows_are_all_kept_once() {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        std::thread::scope(|scope| {
            for window in 0..16 {
                let settings = &settings;
                scope.spawn(move || {
                    let confirmation = Confirmation {
                        reference: format!("CAM_PASS_{}", window % 8),
                        host: "10.0.0.7".into(),
                    };
                    confirm(settings, &confirmation).unwrap();
                });
            }
        });
        let reloaded = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let state = reloaded.state();
        let kept = state
            .stored("camera.confirmed")
            .and_then(|value| value.as_array());
        assert_eq!(kept.map(Vec::len), Some(8));
    }
}
