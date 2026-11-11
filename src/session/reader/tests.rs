use super::*;
use crate::{access::Policy, network::ScanRequest, plugins::Plugins};
use api_types::{Audience, DocWire};
use camera::{Found, Identity, Login, ScanLoad, ScanPorts, Scheme, Secret, SignIn, Targets, Web};
use serde_json::json;
use sessions::CursorOptions;
use std::{net::Ipv4Addr, sync::Arc};
use workspace::Source;

const PASSWORD: &str = "SECRET-SCAN-PASSWORD";

fn found(last: u8, sign_in: SignIn) -> Found {
    Found {
        addr: Ipv4Addr::new(10, 9, 9, last),
        web: Web {
            scheme: Scheme::Https,
            port: 8443,
        },
        identity: Identity {
            model: "Q1656".into(),
            serial: String::new(),
            firmware: String::new(),
            product_type: String::new(),
            architecture: String::new(),
            soc: String::new(),
        },
        uptime: None,
        sign_in,
    }
}

fn add(address: &str) -> api_types::SessionCommand {
    serde_json::from_value(json!({"type": "content", "command": {
        "type": "addItems", "open": true, "items": [{"type": "Live", "found": address}]}}))
    .unwrap()
}

/// A camera the scan signed in to becomes an opened stream with the login that worked, and one it did not is refused;
/// neither the answer nor what the Doc shows holds the password.
#[tokio::test]
async fn adding_a_found_camera_uses_the_scan_login_and_never_shows_it() {
    let folder = tempfile::tempdir().unwrap();
    // Stream defaults that are not the built-in ones: a found camera must get these, not constants.
    let path = folder.path().join("default.json");
    std::fs::write(
        &path,
        r#"{"version": 1, "settings": {"camera.head": 3, "camera.rtsp_port": 8554,
            "datahub.topic": "custom.topic", "datahub.channel": 4}}"#,
    )
    .unwrap();
    let settings = settings::Settings::load(&path).unwrap();
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
    let network = crate::network::Network::with_scanner(|_| {
        let (tx, rx) = mpsc::channel(4);
        tx.try_send(Ok(found(
            2,
            SignIn::SignedIn {
                login_index: 0,
                architecture: String::new(),
                soc: String::new(),
            },
        )))
        .unwrap();
        tx.try_send(Ok(found(3, SignIn::Refused))).unwrap();
        rx
    });
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    )
    .with_network(Arc::clone(&network));
    network.rescan(ScanRequest {
        targets: Targets::parse("10.9.9.0/30").unwrap(),
        logins: vec![Login {
            user: "root".into(),
            password: Secret::new(PASSWORD),
        }],
        load: ScanLoad::Normal,
        ports: ScanPorts::preferring(Scheme::Https),
    });
    let mut rows = network.subscribe();
    rows.wait_for(|snapshot| snapshot.rows.len() == 2)
        .await
        .unwrap();
    let handle = state
        .sessions
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();

    // Only the owner has the scan's logins.
    assert!(matches!(
        wire::scope(&add("10.9.9.2")),
        Some(Scope::Control)
    ));
    let refused = answer(&state, &handle, 1, add("10.9.9.3")).await;
    assert!(matches!(
        refused,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Refused {
                code: sessions::session::Refusal::Invalid
            },
            ..
        }
    ));
    assert!(
        handle
            .state()
            .doc
            .workspace
            .parts()
            .workspace
            .items
            .is_empty()
    );

    let ack = answer(&state, &handle, 2, add("10.9.9.2")).await;
    assert!(matches!(
        ack,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Ok,
            ..
        }
    ));
    let doc = handle.state().doc.clone();
    let parts = doc.workspace.parts();
    let [item] = parts.workspace.items.as_slice() else {
        panic!("one stream was added");
    };
    let Source::Live(recipe) = &item.recipe.source else {
        panic!("a live stream");
    };
    assert_eq!(
        (recipe.host.as_str(), recipe.username.as_str(), recipe.port),
        ("10.9.9.2", "root", 8443)
    );
    assert_eq!(recipe.password.expose(), PASSWORD);
    // Everything the scan does not know is the configured default.
    assert_eq!((recipe.head, recipe.rtsp_port), (3, 8554));
    assert_eq!(
        recipe.overlay,
        workspace::LiveOverlay::DataHub {
            handler: "ADF_V1_FRAME".into(),
            topic: "custom.topic".into(),
            channel: 4
        }
    );

    let shown: DocWire = crate::session::project::project(&doc, Audience::HOST, false);
    for text in [
        serde_json::to_string(&ack).unwrap(),
        serde_json::to_string(&shown).unwrap(),
    ] {
        assert!(!text.contains(PASSWORD), "{text}");
    }
}

fn add_live(fields: serde_json::Value) -> api_types::SessionCommand {
    let mut item = json!({"type": "Live"});
    item.as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    serde_json::from_value(json!({"type": "content", "command": {
        "type": "addItems", "open": false, "items": [item]}}))
    .unwrap()
}

/// Another stream of a camera whose saved login is a literal password takes that login on the server, only for the
/// owner and only for the same camera; the password is in no answer and nothing the Doc shows.
#[tokio::test]
async fn another_stream_of_a_camera_copies_its_saved_login() {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    );
    let owner = state
        .sessions
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    let ok = |ack: &SessionServerMessage| {
        matches!(
            ack,
            SessionServerMessage::Ack {
                outcome: AckOutcome::Ok,
                ..
            }
        )
    };
    let mut acks = Vec::new();
    acks.push(
        answer(
            &state,
            &owner,
            1,
            add_live(json!({"host": "cam.local", "username": "root", "password": PASSWORD})),
        )
        .await,
    );
    let source = owner.state().doc.workspace.parts().workspace.items[0]
        .id
        .to_string();

    // The login of each stream added after the first, in the order added.
    let like = |fields: serde_json::Value| {
        let mut fields = fields;
        fields["like"] = json!(source);
        add_live(fields)
    };
    acks.push(
        answer(
            &state,
            &owner,
            2,
            like(json!({"host": "cam.local", "username": "root"})),
        )
        .await,
    );
    acks.push(
        answer(
            &state,
            &owner,
            3,
            like(json!({"host": "other.local", "username": "root"})),
        )
        .await,
    );
    assert!(acks.iter().all(ok));
    let doc = owner.state().doc.clone();
    let logins: Vec<_> = doc
        .workspace
        .parts()
        .workspace
        .items
        .iter()
        .map(|item| match &item.recipe.source {
            Source::Live(recipe) => (
                recipe.host.clone(),
                recipe.username.clone(),
                recipe.password.expose() == PASSWORD,
            ),
            _ => panic!("a live stream"),
        })
        .collect();
    assert_eq!(
        logins,
        [
            ("cam.local".into(), "root".into(), true),
            ("cam.local".into(), "root".into(), true),
            ("other.local".into(), "root".into(), false),
        ]
    );
    let shown: DocWire = crate::session::project::project(&doc, Audience::HOST, false);
    for text in [
        serde_json::to_string(&acks).unwrap(),
        serde_json::to_string(&shown).unwrap(),
    ] {
        assert!(!text.contains(PASSWORD), "{text}");
    }
}

/// Editing a stream keeps its id and, while it stays on the same camera, its saved login when the form leaves the
/// login blank; the password is in no answer and nothing the Doc shows.
#[tokio::test]
async fn editing_a_stream_keeps_its_id_and_saved_login_on_the_same_camera_only() {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    );
    let owner = state
        .sessions
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    let mut acks = vec![
        answer(
            &state,
            &owner,
            1,
            add_live(json!({"host": "cam.local", "username": "root", "password": PASSWORD})),
        )
        .await,
    ];
    let id = owner.state().doc.workspace.parts().workspace.items[0]
        .id
        .to_string();
    // The configured overlay (DataHub), with its handler, which an edit that names none keeps.
    let overlay = || match &owner.state().doc.workspace.parts().workspace.items[0]
        .recipe
        .source
    {
        Source::Live(recipe) => recipe.overlay.clone(),
        _ => panic!("a live stream"),
    };
    let saved_overlay = overlay();
    let edit = |fields: serde_json::Value| {
        let mut new = json!({"type": "Live"});
        new.as_object_mut()
            .unwrap()
            .extend(fields.as_object().unwrap().clone());
        serde_json::from_value(json!({"type": "content", "command": {
            "type": "editItem", "item": id, "new": new}}))
        .unwrap()
    };
    acks.push(
        answer(
            &state,
            &owner,
            2,
            edit(json!({"host": "cam.local", "label": "Gate"})),
        )
        .await,
    );
    let login = || match &owner.state().doc.workspace.parts().workspace.items[..] {
        [item] => match &item.recipe.source {
            Source::Live(recipe) => (
                item.id.to_string(),
                item.label.clone(),
                recipe.password.expose() == PASSWORD,
            ),
            _ => panic!("a live stream"),
        },
        items => panic!("one item, got {}", items.len()),
    };
    assert_eq!(login(), (id.clone(), "Gate".into(), true));
    assert_eq!(overlay(), saved_overlay);

    acks.push(answer(&state, &owner, 3, edit(json!({"host": "other.local"}))).await);
    assert_eq!(login(), (id, String::new(), false));

    let shown: DocWire =
        crate::session::project::project(&owner.state().doc, Audience::HOST, false);
    for text in [
        serde_json::to_string(&acks).unwrap(),
        serde_json::to_string(&shown).unwrap(),
    ] {
        assert!(!text.contains(PASSWORD), "{text}");
    }
}

/// An edit that names no overlay keeps the saved one with its hidden broker password.
#[tokio::test]
async fn an_edit_without_an_overlay_never_clears_a_saved_broker_password() {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    );
    let owner = state
        .sessions
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    let mqtt = json!({"mode": "mqtt", "handler": "ADF_V1_FRAME", "broker_host": "b.local", "broker_user": "mqtt",
        "broker_pass": PASSWORD, "source_key": "k"});
    answer(
        &state,
        &owner,
        1,
        add_live(json!({"host": "cam.local", "overlay": mqtt})),
    )
    .await;
    let id = owner.state().doc.workspace.parts().workspace.items[0]
        .id
        .to_string();
    let broker_pass = || match &owner.state().doc.workspace.parts().workspace.items[0]
        .recipe
        .source
    {
        Source::Live(recipe) => recipe.mqtt_settings().map(|mqtt| mqtt.broker_pass),
        _ => panic!("a live stream"),
    };
    let edit = |fields: serde_json::Value| {
        let mut new = json!({"type": "Live", "host": "cam.local"});
        new.as_object_mut()
            .unwrap()
            .extend(fields.as_object().unwrap().clone());
        serde_json::from_value(json!({"type": "content", "command": {
            "type": "editItem", "item": id, "new": new}}))
        .unwrap()
    };
    let relabel = |label: &str| edit(json!({"label": label}));
    let kept = answer(&state, &owner, 3, relabel("Gate")).await;
    assert!(matches!(
        kept,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Ok,
            ..
        }
    ));
    assert_eq!(
        broker_pass().map(|secret| secret.expose().to_owned()),
        Some(PASSWORD.to_owned())
    );

    // The same with the overlay named and its password left blank, and with the broker kept for another mode: the
    // blank password is the saved one.
    let broker = json!({"broker_host": "b.local", "broker_user": "mqtt", "source_key": "k"});
    let mut explicit = broker.clone();
    explicit["mode"] = json!("mqtt");
    explicit["handler"] = json!("ADF_V1_FRAME");
    let rtsp = json!({"mode": "rtsp", "handler": "ONVIF_XML"});
    let hub = json!({"topic": "t", "channel": 7});
    let mut seq = 3;
    for fields in [
        json!({"overlay": explicit}),
        json!({"overlay": rtsp, "kept_mqtt": broker, "kept_datahub": hub}),
        // The password now saved only as kept.
        json!({"overlay": rtsp, "kept_mqtt": broker, "kept_datahub": hub, "label": "Again"}),
    ] {
        seq += 1;
        let reply = answer(&state, &owner, seq, edit(fields)).await;
        assert!(matches!(
            reply,
            SessionServerMessage::Ack {
                outcome: AckOutcome::Ok,
                ..
            }
        ));
        assert_eq!(
            broker_pass().map(|secret| secret.expose().to_owned()),
            Some(PASSWORD.to_owned())
        );
    }

    // A relabel that sends no overlay or kept settings, of a stream whose MQTT is kept, changes none of them.
    let kept = || match &owner.state().doc.workspace.parts().workspace.items[0]
        .recipe
        .source
    {
        Source::Live(recipe) => recipe.kept.clone(),
        _ => panic!("a live stream"),
    };
    let before = kept();
    assert_eq!(before.datahub.as_ref().map(|hub| hub.channel), Some(7));
    seq += 1;
    let reply = answer(&state, &owner, seq, relabel("Owner")).await;
    assert!(matches!(
        reply,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Ok,
            ..
        }
    ));
    assert_eq!(kept(), before);
}
