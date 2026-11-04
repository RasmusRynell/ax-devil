use super::*;
use crate::{Confirmation, Content, Environment, LiveOverlay, LiveRecipe, Protocol, Secret};
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

fn env(variables: &[(&str, &str)], confirmed: &[(&str, &str)]) -> Environment {
    let variables: HashMap<String, String> = variables
        .iter()
        .map(|(k, v)| ((*k).into(), (*v).into()))
        .collect();
    Environment::new(
        move |name| variables.get(name).cloned(),
        confirmed.iter().map(|(reference, host)| Confirmation {
            reference: (*reference).into(),
            host: (*host).into(),
        }),
    )
}

const TARGET: [(&str, &str); 3] = [
    ("AX_DEVIL_TARGET_ADDR", "192.0.2.7"),
    ("AX_DEVIL_TARGET_USER", "root"),
    ("AX_DEVIL_TARGET_PASS", "hunter2-secret"),
];

#[test]
fn a_credential_reference_expands_with_its_own_host_or_a_confirmed_one_and_otherwise_asks() {
    let item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    // Bound: the target set together expands, with no confirmation.
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&TARGET, &[])) else {
        panic!("the bound set expands");
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

    // Unbound: another host with the target's password. The error names the reference and the host, never the value.
    let other = NewLive {
        host: Some("192.0.2.99".into()),
        ..NewLive::default()
    };
    let item = live(other, &defaults(), &Handlers::built_in()).unwrap();
    let ask = |variables: &[(&str, &str)], confirmed: &[(&str, &str)]| {
        resolve(&item, &env(variables, confirmed))
    };
    let error = ask(&TARGET, &[]).unwrap_err();
    assert_eq!(error.code, ErrorCode::NeedsConfirmation);
    assert_eq!(error.location, Some(Location::Item(item.id)));
    assert!(error.reason.contains("$AX_DEVIL_TARGET_USER") && error.reason.contains("192.0.2.99"));
    assert!(!error.reason.contains("hunter2"));
    assert_eq!(
        error
            .confirmations
            .iter()
            .map(|c| (c.reference.as_str(), c.host.as_str()))
            .collect::<Vec<_>>(),
        [
            ("AX_DEVIL_TARGET_USER", "192.0.2.99"),
            ("AX_DEVIL_TARGET_PASS", "192.0.2.99")
        ]
    );
    assert!(!format!("{:?}", error.confirmations).contains("hunter2"));
    let Source::Live(live_recipe) = &item.recipe.source else {
        unreachable!()
    };
    let asked: Vec<_> = live_recipe
        .needs_confirmation(&env(&TARGET, &[]))
        .into_iter()
        .map(|c| c.reference)
        .collect();
    assert_eq!(asked, ["AX_DEVIL_TARGET_USER", "AX_DEVIL_TARGET_PASS"]);

    // Confirmed for that host (and only that one, whatever its case): both credentials expand.
    let both = [
        ("AX_DEVIL_TARGET_USER", "192.0.2.99"),
        ("AX_DEVIL_TARGET_PASS", "192.0.2.99"),
    ];
    assert!(ask(&TARGET, &both).is_ok());
    let elsewhere = [
        ("AX_DEVIL_TARGET_USER", "192.0.2.7"),
        ("AX_DEVIL_TARGET_PASS", "192.0.2.7"),
    ];
    assert_eq!(
        ask(&TARGET, &elsewhere).unwrap_err().code,
        ErrorCode::NeedsConfirmation
    );

    // A stream URL on another host receives the login too, so the binding no longer covers it.
    let sneaky = NewLive {
        url: Some("rtsp://203.0.113.5/stream".into()),
        ..NewLive::default()
    };
    let item = live(sneaky, &defaults(), &Handlers::built_in()).unwrap();
    let error = resolve(&item, &env(&TARGET, &[])).unwrap_err();
    assert!(error.reason.contains("203.0.113.5"), "{}", error.reason);
    // An unset variable sends nothing, so it asks nothing.
    let unset = env(&[("AX_DEVIL_TARGET_ADDR", "192.0.2.7")], &[]);
    let item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    let Ok(Content::LiveVideo(video)) = resolve(&item, &unset) else {
        panic!("resolves")
    };
    assert_eq!(video.camera.login, None);
    assert_eq!(
        resolve(&item, &env(&[], &[])).unwrap_err().reason,
        "Enter the device host."
    );
}

#[test]
fn the_broker_set_expands_with_the_broker_host_and_its_login_is_not_the_cameras() {
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
    // The camera receives the broker login to pass on, so a camera that is not the broker needs the user's word.
    let error = resolve(&item, &env(&broker, &[])).unwrap_err();
    assert_eq!(error.code, ErrorCode::NeedsConfirmation);
    assert_eq!(
        error
            .confirmations
            .iter()
            .map(|c| (c.reference.as_str(), c.host.as_str()))
            .collect::<Vec<_>>(),
        [
            ("AX_DEVIL_MQTT_BROKER_USER", "192.0.2.7"),
            ("AX_DEVIL_MQTT_BROKER_PASS", "192.0.2.7")
        ]
    );
    assert!(!format!("{error:?}").contains("broker-secret"));
    let to_camera = [
        ("AX_DEVIL_MQTT_BROKER_USER", "192.0.2.7"),
        ("AX_DEVIL_MQTT_BROKER_PASS", "192.0.2.7"),
    ];
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&broker, &to_camera)) else {
        panic!("the broker set expands once confirmed for the camera");
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
    // The camera's login is a literal here, so only the broker references are in question; point them at another host.
    let mut other = recipe(&item).clone();
    other.overlay = LiveOverlay::Mqtt {
        handler: "ADF_V1_FRAME".into(),
        broker_host: "198.51.100.77".into(),
        broker_port: 1883,
        broker_user: "$AX_DEVIL_MQTT_BROKER_USER".into(),
        broker_pass: Secret::new("$AX_DEVIL_MQTT_BROKER_PASS"),
        source_key: "k".into(),
    };
    let asked: Vec<_> = other
        .needs_confirmation(&env(&broker, &[]))
        .into_iter()
        .map(|c| (c.reference, c.host))
        .collect();
    assert_eq!(asked.len(), 4, "the other broker host and the camera");
    assert!(asked.iter().any(|(_, host)| host == "198.51.100.77"));
    assert!(asked.iter().any(|(_, host)| host == "192.0.2.7"));
}

#[test]
fn a_camera_that_is_the_broker_host_needs_no_second_confirmation() {
    let broker = [
        ("AX_DEVIL_MQTT_BROKER_ADDR", "192.0.2.7"),
        ("AX_DEVIL_MQTT_BROKER_USER", "mqtt"),
        ("AX_DEVIL_MQTT_BROKER_PASS", "broker-secret"),
    ];
    let new = NewLive {
        overlay: Some(NewLiveOverlay::Mqtt(NewMqtt::default())),
        ..host("192.0.2.7")
    };
    let item = live(new, &defaults(), &Handlers::built_in()).unwrap();
    assert!(resolve(&item, &env(&broker, &[])).is_ok());
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

#[test]
fn another_stream_copies_the_saved_login_only_for_the_same_camera() {
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
    };
    let copy = |change: fn(&mut NewLive)| {
        let mut new = host("CAM.local");
        change(&mut new);
        new.copy_login(&saved, &defaults()).unwrap();
        new
    };
    let login = |new: &NewLive| {
        (
            new.username.clone().unwrap_or_default(),
            new.password
                .as_ref()
                .map(|secret| secret.expose().to_owned()),
        )
    };

    // The same host, with the standard port written either way: the user and password fill in.
    let same = (String::from("root"), Some(String::from("literal")));
    assert_eq!(login(&copy(|_| {})), same);
    assert_eq!(login(&copy(|new| new.port = Some(443))), same);
    // A password the person entered stays.
    let typed = copy(|new| new.password = Some(Secret::new("typed")));
    assert_eq!(login(&typed).1.as_deref(), Some("typed"));
    // Another host, protocol, port or user gets nothing of the saved password.
    for change in [
        (|new: &mut NewLive| new.host = Some("other.local".into())) as fn(&mut NewLive),
        |new| new.protocol = Some(Protocol::Http),
        |new| new.port = Some(8443),
        |new| new.username = Some("viewer".into()),
    ] {
        assert_eq!(login(&copy(change)).1, None);
    }
}

#[test]
fn a_stream_url_elsewhere_gets_no_saved_login() {
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
    };
    let copy = |url: &str| {
        let mut new = NewLive {
            url: Some(url.into()),
            ..host("cam.local")
        };
        new.copy_login(&saved, &defaults()).map(|()| new.password)
    };
    // The camera's own RTSP address, spelled in the URL, takes the login; another host or port is refused.
    assert_eq!(
        copy("rtsp://CAM.local/stream").unwrap().unwrap().expose(),
        "literal"
    );
    for url in ["rtsp://other.example/s", "rtsp://cam.local:8554/s"] {
        let error = copy(url).unwrap_err();
        assert_eq!(error.location, Some(Location::field("password")));
    }
    // With the login entered there is nothing to copy, so the URL is free.
    let mut typed = NewLive {
        url: Some("rtsp://other.example/s".into()),
        username: Some("root".into()),
        password: Some(Secret::new("typed")),
        ..host("cam.local")
    };
    typed.copy_login(&saved, &defaults()).unwrap();
}
