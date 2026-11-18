//! Editing a stream: what a cleared field and the modes not chosen keep.

use super::*;

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
        rtsp_route: RtspRoute::Direct,
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
    // A login cleared on purpose sends nothing, so a changed RTSP port is no reason to refuse; one that would be
    // copied to the changed port is refused.
    let moved = |login: Option<String>| NewLive {
        username: login.clone(),
        password: login.map(Secret::new),
        rtsp_port: Some(8554),
        overlay: Some(NewLiveOverlay::None),
        ..host("cam.local")
    };
    let mut cleared = moved(Some(String::new()));
    cleared
        .copy_login(&saved, &editing, LoginUse::Stream)
        .unwrap();
    let item = live(cleared, &editing, &Handlers::built_in()).unwrap();
    assert_eq!(
        (
            recipe(&item).username.as_str(),
            recipe(&item).password.expose()
        ),
        ("", "")
    );
    assert!(
        moved(None)
            .copy_login(&saved, &editing, LoginUse::Stream)
            .is_err()
    );
    assert_eq!(edited.kept.mqtt, saved.kept.mqtt);
    assert_eq!(
        edited.kept.datahub,
        Some(crate::DataHubSettings {
            topic: "t".into(),
            channel: 4
        })
    );

    // A password given as empty (an edit) restores only a literal the client was never sent, so a reference it was
    // sent stays cleared; one not given (adding another stream) is the saved one, reference or literal.
    let by_reference = |overlay: LiveOverlay, mqtt: Option<crate::MqttSettings>| {
        let mut source = saved.clone();
        source.password = Secret::new("$CAM_PW");
        source.overlay = overlay;
        source.kept.mqtt = mqtt;
        source
    };
    let in_mqtt = |source: &LiveRecipe, active: bool, given: Option<Secret>| {
        let broker = NewMqtt {
            handler: Some("ADF_V1_FRAME".into()),
            broker_host: Some("b.local".into()),
            broker_port: Some(1884),
            broker_user: Some("mqtt".into()),
            broker_pass: given.clone(),
            source_key: Some("key".into()),
        };
        let mut new = NewLive {
            password: given,
            overlay: Some(if active {
                NewLiveOverlay::Mqtt(broker.clone())
            } else {
                NewLiveOverlay::None
            }),
            kept_mqtt: Some(broker),
            ..host("cam.local")
        };
        new.copy_login(source, &editing, LoginUse::Stream).unwrap();
        let item = live(new, &editing, &Handlers::built_in()).unwrap();
        let stream = recipe(&item);
        let broker_pass = match (&stream.overlay, &stream.kept.mqtt) {
            (LiveOverlay::Mqtt { broker_pass, .. }, _) => broker_pass,
            (_, Some(kept)) => &kept.broker_pass,
            _ => panic!("MQTT is the overlay or kept"),
        };
        (
            stream.password.expose().to_owned(),
            broker_pass.expose().to_owned(),
        )
    };
    let reference = Secret::new("$BROKER_PW");
    let active = by_reference(
        LiveOverlay::Mqtt {
            handler: "ADF_V1_FRAME".into(),
            broker_host: "b.local".into(),
            broker_port: 1884,
            broker_user: "mqtt".into(),
            broker_pass: reference.clone(),
            source_key: "key".into(),
        },
        None,
    );
    let kept = by_reference(
        LiveOverlay::None,
        Some(crate::MqttSettings {
            broker_host: "b.local".into(),
            broker_port: 1884,
            broker_user: "mqtt".into(),
            broker_pass: reference,
            source_key: "key".into(),
        }),
    );
    for (source, overlay) in [(&active, true), (&kept, false)] {
        let cleared = in_mqtt(source, overlay, Some(Secret::new("")));
        assert_eq!(cleared, (String::new(), String::new()));
        let added = in_mqtt(source, overlay, None);
        assert_eq!(added, ("$CAM_PW".to_owned(), "$BROKER_PW".to_owned()));
    }

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

#[test]
fn a_stream_url_sent_back_without_its_password_keeps_the_saved_one_but_a_changed_url_does_not() {
    let saved = LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: Some("http://root:s3cret@cam.local/axis-media/media.amp".into()),
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let copy = |url: &str| {
        let mut new = NewLive {
            url: Some(url.into()),
            ..host("cam.local")
        };
        new.copy_login(&saved, &defaults(), LoginUse::Stream)
            .map(|()| new.url)
    };
    assert_eq!(
        copy("http://root@cam.local/axis-media/media.amp")
            .unwrap()
            .as_deref(),
        saved.url.as_deref()
    );
    // Another URL keeps what was typed, and the camera's login goes to its server only if the URL has none of its own.
    assert_eq!(
        copy("http://root@other.local/axis-media/media.amp")
            .unwrap()
            .as_deref(),
        Some("http://root@other.local/axis-media/media.amp")
    );
    assert!(copy("http://other.local/axis-media/media.amp").is_err());
}
