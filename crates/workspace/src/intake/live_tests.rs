mod editing;
mod login;
mod privacy;

use super::*;
use crate::{Content, Environment, LiveOverlay, LiveRecipe, Protocol, RtspRoute, Secret};
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
        rtsp_route: RtspRoute::Direct,
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

    let mut padded = defaults();
    padded.host = " 10.0.0.2 ".into();
    padded.mqtt.host = " 10.0.0.9 ".into();
    padded.overlay = LiveMode::Mqtt;
    let item = live(NewLive::default(), &padded, &Handlers::built_in()).unwrap();
    let LiveOverlay::Mqtt { broker_host, .. } = &recipe(&item).overlay else {
        panic!("the configured mode is MQTT");
    };
    assert_eq!(
        (recipe(&item).host.as_str(), broker_host.as_str()),
        ("10.0.0.2", "10.0.0.9")
    );
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
fn the_route_is_the_configured_one_unless_entered_and_a_stream_url_is_kept_as_typed() {
    let mut configured = defaults();
    configured.rtsp_route = RtspRoute::WebApi;
    let item = live(host("h"), &configured, &Handlers::built_in()).unwrap();
    assert_eq!(recipe(&item).rtsp_route, RtspRoute::WebApi);
    let mut new = host("h");
    new.rtsp_route = Some(RtspRoute::Direct);
    let item = live(new, &configured, &Handlers::built_in()).unwrap();
    assert_eq!(recipe(&item).rtsp_route, RtspRoute::Direct);
    // Whether a URL can be streamed is for the camera to say when it connects (owner decision LIV-006).
    for url in [
        "rtsp://root:pw@cam/stream",
        "https://cam/stream",
        "rtsps://cam/stream",
        "not a url",
    ] {
        let mut new = host("h");
        new.url = Some(url.into());
        let item = live(new, &defaults(), &Handlers::built_in()).unwrap();
        assert_eq!(recipe(&item).url.as_deref(), Some(url));
    }
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
            datahub(NewDataHub {
                topic: Some("t".into()),
                channel: Some(i64::from(u32::MAX) + 1),
                ..NewDataHub::default()
            }),
            "channel",
            "Channel ID must be at most 4294967295.",
        ),
        (
            with(&|n| {
                n.overlay = Some(NewLiveOverlay::Rtsp {
                    handler: Some("NOPE".into()),
                })
            }),
            "handler",
            "Unknown live overlay handler type. List them with: ax-devil list-handlers",
        ),
        // The transport's own fields are checked before the handler.
        (
            mqtt(&|m| (m.broker_host, m.handler) = (Some(" ".into()), Some("NOPE".into()))),
            "broker_host",
            "MQTT Host is required for MQTT overlay mode.",
        ),
        (
            datahub(NewDataHub {
                handler: Some("NOPE".into()),
                ..NewDataHub::default()
            }),
            "topic",
            "Topic is required for WebSocket overlay mode.",
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
        &item.label,
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
fn a_custom_label_names_the_content_and_an_unnamed_stream_is_named_by_its_expanded_host() {
    let mut item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    let label_of = |item: &WorkspaceItem| match resolve(item, &env(&TARGET)) {
        Ok(Content::LiveVideo(video)) => video.label,
        _ => panic!("the references expand"),
    };
    assert_eq!(label_of(&item), "192.0.2.7");
    assert_eq!(item.display_name_in(&env(&TARGET)), "192.0.2.7");
    assert_eq!(item.display_name_in(&env(&[])), "Live stream");
    // A label the user set is theirs, even when it reads like a default: the placeholder of an unnamed stream on a
    // reference, or the host it expands to. The Content and the row agree.
    for own in ["Gate", "Live stream", "192.0.2.7"] {
        item.label = own.into();
        assert_eq!(label_of(&item), own);
        assert_eq!(item.display_name_in(&env(&TARGET)), own);
    }
}

#[test]
fn a_label_that_is_the_current_default_clears_whatever_that_default_is_in_the_namers_environment() {
    let named = |new: NewLive, label, env: &Environment| {
        let item = live(new, &defaults(), &Handlers::built_in()).unwrap();
        with_label(item, Some(label), env).label
    };
    let none = env(&[]);
    // A literal host is its own default, so naming the stream by it names nothing.
    assert_eq!(named(host("192.0.2.7"), "192.0.2.7", &none), "");
    assert_eq!(
        named(host("192.0.2.7"), "Live stream", &none),
        "Live stream"
    );
    // The default of a stream on a reference is the variable's value, else `Live stream`.
    assert_eq!(named(NewLive::default(), "Live stream", &none), "");
    assert_eq!(named(NewLive::default(), "192.0.2.7", &none), "192.0.2.7");
    assert_eq!(named(NewLive::default(), "192.0.2.7", &env(&TARGET)), "");
    assert_eq!(
        named(NewLive::default(), "Live stream", &env(&TARGET)),
        "Live stream"
    );
    assert_eq!(named(NewLive::default(), "Gate", &env(&TARGET)), "Gate");
    // A namer whose references are not expanded compares with the text as typed.
    let literal = Environment::literal();
    assert_eq!(
        named(NewLive::default(), "$AX_DEVIL_TARGET_ADDR", &literal),
        ""
    );
    assert_eq!(
        named(NewLive::default(), "Live stream", &literal),
        "Live stream"
    );
}

#[test]
fn a_stored_label_that_repeats_the_default_still_names_the_content_and_the_row_alike() {
    // A file written before labels were cleared at set time: at run time only an empty label is unset.
    let mut item = live(NewLive::default(), &defaults(), &Handlers::built_in()).unwrap();
    item.label = "Live stream".into();
    let Ok(Content::LiveVideo(video)) = resolve(&item, &env(&TARGET)) else {
        panic!("the references expand");
    };
    assert_eq!(video.label, "Live stream");
    assert_eq!(item.display_name_in(&env(&TARGET)), "Live stream");
    assert_eq!(item.display_name(), "Live stream");
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
        "This handler cannot decode a live overlay; select another handler type."
    );
}
