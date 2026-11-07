mod login;

use super::*;
use crate::{Content, Environment, LiveOverlay, LiveRecipe, Protocol, Secret};
use foundation::{CancelToken, Interrupt, Location};
use std::collections::HashMap;

fn defaults() -> LiveDefaults {
    LiveDefaults {
        host: "$AX_DEVIL_TARGET_ADDR".into(),
        username: "$AX_DEVIL_TARGET_USER".into(),
        password: Secret::new("$AX_DEVIL_TARGET_PASS"),
        protocol: Protocol::Https,
        head: 1,
        rtsp_port: 554,
        resolution: String::new(),
        overlay: LiveMode::DataHub,
        rtsp_handler: "ONVIF_XML".into(),
        mqtt: MqttDefaults {
            handler: "ADF_V1_FRAME".into(),
            host: "$AX_DEVIL_MQTT_BROKER_ADDR".into(),
            user: "$AX_DEVIL_MQTT_BROKER_USER".into(),
            password: Secret::new("$AX_DEVIL_MQTT_BROKER_PASS"),
            port: 1883,
            source_key: "com.axis.scene.frame.v1#1".into(),
        },
        datahub: DataHubDefaults {
            handler: "ADF_V1_FRAME".into(),
            topic: "com.axis.scene.frame.v1".into(),
            channel: 1,
        },
    }
}

fn host(host: &str) -> NewLive {
    NewLive {
        host: Some(host.into()),
        ..NewLive::default()
    }
}

fn recipe(item: &WorkspaceItem) -> &LiveRecipe {
    match &item.recipe.source {
        Source::Live(recipe) => recipe,
        _ => panic!("a live item has a live recipe"),
    }
}

/// The defaults without a broker, data source or topic: the overlay modes must be completed by the user.
fn bare() -> LiveDefaults {
    let mut bare = defaults();
    bare.mqtt.host.clear();
    bare.mqtt.source_key.clear();
    bare.datahub.topic.clear();
    bare
}

fn refusal(new: NewLive) -> (ErrorCode, String, Option<Location>) {
    let error = live(new, &bare(), &Handlers::built_in()).unwrap_err();
    (error.code, error.reason, error.location)
}

#[test]
fn an_empty_form_takes_the_configured_defaults_as_written_and_an_entered_value_wins() {
    let item = live(host("192.0.2.7"), &defaults(), &Handlers::built_in()).unwrap();
    let entered = recipe(&item);
    assert_eq!(item.label, "", "a derived name is never stored");
    assert_eq!(item.display_name(), "192.0.2.7");
    assert_eq!(
        (entered.username.as_str(), entered.password.expose()),
        ("$AX_DEVIL_TARGET_USER", "$AX_DEVIL_TARGET_PASS")
    );
    assert_eq!(
        (
            entered.protocol,
            entered.port,
            entered.head,
            entered.rtsp_port
        ),
        (Protocol::Https, 0, 1, 554)
    );
    assert_eq!(
        entered.overlay,
        LiveOverlay::DataHub {
            handler: "ADF_V1_FRAME".into(),
            topic: "com.axis.scene.frame.v1".into(),
            channel: 1
        }
    );
    let item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    assert_eq!(recipe(&item).host, "$AX_DEVIL_TARGET_ADDR");
    assert_eq!(item.display_name(), "Live stream");
}

#[test]
fn entered_credentials_are_kept_exactly_and_the_resolution_is_the_one_given_or_the_cameras_own() {
    let mut configured = defaults();
    configured.resolution = "1280x720".into();
    let mut new = host("192.0.2.7");
    new.username = Some(" root ".into());
    new.password = Some(Secret::new(" p w "));
    let item = live(new, &configured, &Handlers::built_in()).unwrap();
    let entered = recipe(&item);
    assert_eq!(entered.username, " root ");
    assert_eq!(entered.password.expose(), " p w ");
    assert_eq!(
        entered.resolution, None,
        "a configured resolution is not inherited"
    );

    let mut new = host("192.0.2.7");
    new.resolution = Some("640x480".into());
    let item = live(new, &configured, &Handlers::built_in()).unwrap();
    assert_eq!(recipe(&item).resolution.as_deref(), Some("640x480"));
}

#[test]
fn what_cannot_be_streamed_is_refused_naming_the_field_and_the_python_message() {
    let with = |change: &dyn Fn(&mut NewLive)| {
        let mut new = host("192.0.2.7");
        change(&mut new);
        new
    };
    let mqtt = |change: &dyn Fn(&mut NewMqtt)| {
        with(&|new| {
            let mut mqtt = NewMqtt::default();
            change(&mut mqtt);
            new.overlay = Some(NewLiveOverlay::Mqtt(mqtt));
        })
    };
    let datahub =
        |hub: NewDataHub| with(&|new| new.overlay = Some(NewLiveOverlay::DataHub(hub.clone())));
    let cases: Vec<(NewLive, &str, &str)> = vec![
        (
            with(&|n| n.head = Some(0)),
            "head",
            "Camera Head must be a positive integer.",
        ),
        (
            with(&|n| n.head = Some(-3)),
            "head",
            "Camera Head must be a positive integer.",
        ),
        (
            with(&|n| n.head = Some(10_000)),
            "head",
            "Camera Head must be at most 9999.",
        ),
        (
            with(&|n| n.head = Some(70_000)),
            "head",
            "Camera Head must be at most 9999.",
        ),
        (
            with(&|n| n.port = Some(70_000)),
            "port",
            "Port must be between 0 and 65535.",
        ),
        (
            with(&|n| n.port = Some(-1)),
            "port",
            "Port must be between 0 and 65535.",
        ),
        (
            with(&|n| n.rtsp_port = Some(0)),
            "rtsp_port",
            "RTSP Port must be between 1 and 65535.",
        ),
        (
            with(&|n| n.rtsp_port = Some(65_536)),
            "rtsp_port",
            "RTSP Port must be between 1 and 65535.",
        ),
        (
            with(&|n| n.port = Some(80)),
            "port",
            "HTTPS cannot use port 80; use the standard port.",
        ),
        (
            with(&|n| (n.protocol, n.port) = (Some(Protocol::Http), Some(443))),
            "port",
            "HTTP cannot use port 443; use the standard port.",
        ),
        (
            with(&|n| n.url = Some("http://cam/stream".into())),
            "url",
            "The stream URL must start with rtsp://.",
        ),
        (
            with(&|n| n.url = Some("rtsps://cam/stream".into())),
            "url",
            "The stream URL must start with rtsp://.",
        ),
        (
            with(&|n| n.url = Some("rtsp://root:pw@cam/stream".into())),
            "url",
            "The stream URL must not contain a login.",
        ),
        (
            mqtt(&|m| m.broker_host = Some("  ".into())),
            "broker_host",
            "MQTT Host is required for MQTT overlay mode.",
        ),
        (
            mqtt(&|m| m.broker_host = Some("b".into())),
            "source_key",
            "Data Source is required for MQTT overlay mode.",
        ),
        (
            datahub(NewDataHub {
                channel: Some(5),
                ..NewDataHub::default()
            }),
            "topic",
            "Topic is required for WebSocket overlay mode.",
        ),
        (
            datahub(NewDataHub {
                topic: Some("t".into()),
                channel: Some(0),
                ..NewDataHub::default()
            }),
            "channel",
            "Channel ID must be a positive integer.",
        ),
        (
            with(&|n| {
                n.overlay = Some(NewLiveOverlay::Rtsp {
                    handler: Some("NOPE".into()),
                })
            }),
            "handler",
            "Unknown live overlay handler type: NOPE",
        ),
    ];
    for (new, field, message) in cases {
        let (_, reason, location) = refusal(new.clone());
        assert_eq!(
            (reason.as_str(), location),
            (message, Some(Location::field(field))),
            "{new:?}"
        );
    }
    // A mode with no configured decoder asks for one.
    let mut no_handler = defaults();
    no_handler.rtsp_handler.clear();
    let new = NewLive {
        overlay: Some(NewLiveOverlay::Rtsp { handler: None }),
        ..host("h")
    };
    let error = live(new, &no_handler, &Handlers::built_in()).unwrap_err();
    assert_eq!(
        error.reason,
        "Select a handler type for RTSP Embedded overlays."
    );
}

#[test]
fn editing_keeps_a_cleared_user_and_what_the_other_overlay_modes_hold() {
    let saved = LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: crate::KeptTransports {
            mqtt: Some(crate::MqttSettings {
                broker_host: "b.local".into(),
                broker_port: 1884,
                broker_user: "mqtt".into(),
                broker_pass: Secret::new("broker-secret"),
                source_key: "key".into(),
            }),
            datahub: None,
        },
    };
    let editing = defaults().for_edit();
    let mut new = NewLive {
        username: Some(String::new()),
        head: Some(9999),
        overlay: Some(NewLiveOverlay::Rtsp {
            handler: Some("ONVIF_XML".into()),
        }),
        kept_mqtt: Some(NewMqtt {
            broker_host: Some("b.local".into()),
            broker_port: Some(1884),
            broker_user: Some("mqtt".into()),
            source_key: Some("key".into()),
            ..NewMqtt::default()
        }),
        kept_datahub: Some(NewDataHub {
            topic: Some("t".into()),
            channel: Some(4),
            ..NewDataHub::default()
        }),
        ..host("cam.local")
    };
    new.copy_login(&saved, &editing, LoginUse::Stream).unwrap();
    let item = live(new, &editing, &Handlers::built_in()).unwrap();
    let edited = recipe(&item);
    assert_eq!(edited.head, 9999);
    assert_eq!(
        (edited.username.as_str(), edited.password.expose()),
        ("", ""),
        "a cleared user stays cleared and gets none of the saved user's password"
    );
    let mut same_user = NewLive {
        overlay: Some(NewLiveOverlay::None),
        ..host("cam.local")
    };
    same_user
        .copy_login(&saved, &editing, LoginUse::Stream)
        .unwrap();
    let item = live(same_user, &editing, &Handlers::built_in()).unwrap();
    assert_eq!(
        (
            recipe(&item).username.as_str(),
            recipe(&item).password.expose()
        ),
        ("root", "literal"),
        "the saved user keeps the saved password"
    );
    assert_eq!(edited.kept.mqtt, saved.kept.mqtt);
    assert_eq!(
        edited.kept.datahub,
        Some(crate::DataHubSettings {
            topic: "t".into(),
            channel: 4
        })
    );

    // A blank password restores only a literal the client was never sent; a reference it was sent stays cleared.
    let broker = NewMqtt {
        handler: Some("ADF_V1_FRAME".into()),
        broker_host: Some("b.local".into()),
        broker_port: Some(1884),
        broker_user: Some("mqtt".into()),
        source_key: Some("key".into()),
        ..NewMqtt::default()
    };
    let mut by_reference = saved.clone();
    by_reference.password = Secret::new("$CAM_PW");
    by_reference.overlay = LiveOverlay::Mqtt {
        handler: "ADF_V1_FRAME".into(),
        broker_host: "b.local".into(),
        broker_port: 1884,
        broker_user: "mqtt".into(),
        broker_pass: Secret::new("$BROKER_PW"),
        source_key: "key".into(),
    };
    by_reference.kept.mqtt = None;
    let mut new = NewLive {
        overlay: Some(NewLiveOverlay::Mqtt(broker.clone())),
        kept_mqtt: Some(broker),
        ..host("cam.local")
    };
    new.copy_login(&by_reference, &editing, LoginUse::Stream)
        .unwrap();
    let item = live(new, &editing, &Handlers::built_in()).unwrap();
    let LiveOverlay::Mqtt { broker_pass, .. } = &recipe(&item).overlay else {
        panic!("an MQTT overlay");
    };
    assert_eq!(recipe(&item).password.expose(), "");
    assert_eq!(broker_pass.expose(), "");

    // The same for the broker password kept for a mode not chosen.
    by_reference.overlay = LiveOverlay::None;
    by_reference.kept.mqtt = Some(crate::MqttSettings {
        broker_host: "b.local".into(),
        broker_port: 1884,
        broker_user: "mqtt".into(),
        broker_pass: Secret::new("$BROKER_PW"),
        source_key: "key".into(),
    });
    let mut new = NewLive {
        overlay: Some(NewLiveOverlay::None),
        kept_mqtt: Some(NewMqtt {
            broker_host: Some("b.local".into()),
            broker_port: Some(1884),
            broker_user: Some("mqtt".into()),
            ..NewMqtt::default()
        }),
        ..host("cam.local")
    };
    new.copy_login(&by_reference, &editing, LoginUse::Stream)
        .unwrap();
    let item = live(new, &editing, &Handlers::built_in()).unwrap();
    let kept = recipe(&item).kept.mqtt.as_ref().expect("MQTT is kept");
    assert_eq!(kept.broker_pass.expose(), "");

    // A mode not chosen keeps any number as entered; choosing it checks the number.
    let out_of_range = |overlay| NewLive {
        overlay: Some(overlay),
        kept_mqtt: Some(NewMqtt {
            broker_port: Some(65_536),
            ..NewMqtt::default()
        }),
        kept_datahub: Some(NewDataHub {
            channel: Some(1 << 40),
            ..NewDataHub::default()
        }),
        ..host("cam.local")
    };
    let item = live(
        out_of_range(NewLiveOverlay::None),
        &editing,
        &Handlers::built_in(),
    )
    .unwrap();
    let kept = &recipe(&item).kept;
    assert_eq!(kept.mqtt.as_ref().map(|m| m.broker_port), Some(65_536));
    assert_eq!(kept.datahub.as_ref().map(|d| d.channel), Some(1 << 40));

    // Nothing entered takes no configured text, only the numbers.
    let none = NewLive {
        overlay: Some(NewLiveOverlay::None),
        ..host("cam.local")
    };
    let item = live(none, &editing, &Handlers::built_in()).unwrap();
    let bare = recipe(&item);
    assert_eq!((bare.username.as_str(), bare.password.expose()), ("", ""));
    assert_eq!(
        bare.kept
            .mqtt
            .as_ref()
            .map(|mqtt| (mqtt.broker_host.as_str(), mqtt.broker_port)),
        Some(("", 1883))
    );
}

#[test]
fn the_default_broker_password_is_kept_only_by_a_stream_that_uses_the_broker() {
    let mut configured = defaults();
    configured.mqtt.password = Secret::new("literal-broker-secret");
    let with = |overlay| NewLive {
        overlay: Some(overlay),
        ..host("cam.local")
    };
    let item = live(
        with(NewLiveOverlay::None),
        &configured,
        &Handlers::built_in(),
    )
    .unwrap();
    let kept = recipe(&item).kept.mqtt.as_ref().expect("MQTT is kept");
    assert_eq!(kept.broker_pass.expose(), "");

    let item = live(
        with(NewLiveOverlay::Mqtt(NewMqtt::default())),
        &configured,
        &Handlers::built_in(),
    )
    .unwrap();
    let LiveOverlay::Mqtt { broker_pass, .. } = &recipe(&item).overlay else {
        panic!("an MQTT overlay");
    };
    assert_eq!(broker_pass.expose(), "literal-broker-secret");
}

fn resolve(item: &WorkspaceItem, env: &Environment) -> Result<Content, UserError> {
    match item.recipe.resolve(
        &item.id,
        "item",
        &Handlers::built_in(),
        env,
        &CancelToken::new(),
    ) {
        Ok(content) => Ok(content),
        Err(Interrupt::Failed(error)) => Err(error),
        Err(Interrupt::Cancelled) => panic!("nothing cancels"),
    }
}

fn env(variables: &[(&str, &str)]) -> Environment {
    let variables: HashMap<String, String> = variables
        .iter()
        .map(|(k, v)| ((*k).into(), (*v).into()))
        .collect();
    Environment::new(move |name| variables.get(name).cloned())
}

const TARGET: [(&str, &str); 3] = [
    ("AX_DEVIL_TARGET_ADDR", "192.0.2.7"),
    ("AX_DEVIL_TARGET_USER", "root"),
    ("AX_DEVIL_TARGET_PASS", "hunter2-secret"),
];

#[test]
fn references_expand_silently_wherever_they_stand_and_an_unset_one_is_empty() {
    let item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&TARGET)) else {
        panic!("the references expand");
    };
    let login = video.camera.login.as_ref().unwrap();
    assert_eq!(
        (video.camera.host.as_str(), login.user.as_str()),
        ("192.0.2.7", "root")
    );
    assert_eq!(
        (login.password.expose(), video.camera.port),
        ("hunter2-secret", 443)
    );
    assert_eq!(
        (video.label.as_str(), video.slot.as_str()),
        ("192.0.2.7", "live")
    );
    assert!(
        !format!("{video:?}").contains("hunter2"),
        "the password never prints"
    );

    // Another host, a stream URL on one: the same references expand, with nothing to confirm.
    let elsewhere = NewLive {
        host: Some("192.0.2.99".into()),
        url: Some("rtsp://203.0.113.5/stream".into()),
        ..NewLive::default()
    };
    let item = live(elsewhere, &defaults(), &Handlers::built_in()).unwrap();
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&TARGET)) else {
        panic!("the references expand for any host");
    };
    assert_eq!(video.camera.host, "192.0.2.99");
    assert_eq!(video.camera.login.unwrap().user, "root");

    // An unset variable is an empty text: no login, and an empty host is refused.
    let item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&[TARGET[0]])) else {
        panic!("resolves")
    };
    assert_eq!(video.camera.login, None);
    assert_eq!(
        resolve(&item, &env(&[])).unwrap_err().reason,
        "Enter the device host."
    );
}

#[test]
fn the_broker_references_expand_with_the_rest() {
    let new = NewLive {
        overlay: Some(NewLiveOverlay::Mqtt(NewMqtt::default())),
        ..host("192.0.2.7")
    };
    let item = live(new, &defaults(), &Handlers::built_in()).unwrap();
    let broker = [
        ("AX_DEVIL_MQTT_BROKER_ADDR", "198.51.100.2"),
        ("AX_DEVIL_MQTT_BROKER_USER", "mqtt"),
        ("AX_DEVIL_MQTT_BROKER_PASS", "broker-secret"),
    ];
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&broker)) else {
        panic!("the broker set expands");
    };
    let LiveOverlay::Mqtt {
        broker_host,
        broker_pass,
        ..
    } = &video.overlay
    else {
        panic!("mqtt")
    };
    assert_eq!(
        (broker_host.as_str(), broker_pass.expose()),
        ("198.51.100.2", "broker-secret")
    );
}

#[test]
fn defaults_without_secrets_leave_the_passwords_out_of_a_new_stream() {
    let new = NewLive {
        username: Some("viewer".into()),
        overlay: Some(NewLiveOverlay::Mqtt(NewMqtt::default())),
        ..host("attacker.example")
    };
    let item = live(new, &defaults().without_secrets(), &Handlers::built_in()).unwrap();
    let recipe = recipe(&item);
    assert!(recipe.password.is_empty());
    let LiveOverlay::Mqtt { broker_pass, .. } = &recipe.overlay else {
        panic!("an MQTT overlay");
    };
    assert!(broker_pass.is_empty());
    // What is not a secret still comes from the settings.
    assert_eq!(recipe.rtsp_port, 554);
}

#[test]
fn a_frame_keyed_decoder_cannot_be_a_live_overlay() {
    let new = NewLive {
        overlay: Some(NewLiveOverlay::Rtsp {
            handler: Some("CVAT".into()),
        }),
        ..host("h")
    };
    let error = live(new, &bare(), &Handlers::built_in()).unwrap_err();
    assert_eq!(error.location, Some(Location::field("handler")));
    assert_eq!(
        error.reason,
        "CVAT cannot decode a live overlay; select another handler type."
    );
}
