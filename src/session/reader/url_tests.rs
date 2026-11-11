use super::*;
use crate::{access::Policy, plugins::Plugins};
use api_types::{Audience, DocWire};
use serde_json::json;
use sessions::CursorOptions;
use std::sync::Arc;
use tokio::sync::watch;
use workspace::Source;

const PASSWORD: &str = "SECRET-URL-PASSWORD";
/// What the stream keeps for the MQTT mode it does not use: no `$NAME` reference, which a guest may not save.
fn kept() -> serde_json::Value {
    json!({"broker_host": "b.local", "broker_user": "u", "source_key": "k"})
}

fn command(command: serde_json::Value) -> api_types::SessionCommand {
    serde_json::from_value(json!({"type": "content", "command": command})).unwrap()
}

fn is_ok(ack: &SessionServerMessage) -> bool {
    matches!(
        ack,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Ok,
            ..
        }
    )
}

/// An app whose settings `configure` has changed; what keeps it running goes with the first value.
fn app(
    configure: impl FnOnce(&settings::Settings),
) -> ((tempfile::TempDir, watch::Sender<bool>), AppState) {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    configure(&settings);
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (stop, stopping) = watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    );
    ((folder, stop), state)
}

/// A stream URL's password never reaches a client, in any spelling that makes one. The owner who sends the URL back as
/// shown keeps the password.
#[tokio::test]
async fn a_stream_urls_password_is_hidden_from_clients_and_kept_through_an_edit() {
    scenario(
        &format!("http:root:{PASSWORD}@cam.local/s"),
        "http://root@cam.local/s",
        &["http://root@cam.local/s", "HTTP://root@CAM.local:80/s"],
    )
    .await;
    // `rtsp` keeps the case of a host and an explicit default port, though retina reaches the same stream.
    scenario(
        &format!("rtsp://root:{PASSWORD}@cam.local/s"),
        "rtsp://root@cam.local/s",
        &[
            "rtsp://root@cam.local/s",
            "RTSP://root@CAM.LOCAL/s",
            "rtsp://root@cam.local:554/s",
            "rtsp://root@cam.local.:554/s",
        ],
    )
    .await;
    // The user as the connection reads it: `r%6fot` is `root`, however either is written.
    scenario(
        &format!("rtsp://r%6fot:{PASSWORD}@cam.local/s"),
        "rtsp://r%6fot@cam.local/s",
        &["rtsp://root@cam.local/s", "rtsp://r%6Fot@cam.local/s"],
    )
    .await;
}

/// A URL the parser reads as hierarchical but hostless, or as opaque, is saved as typed and shown as a placeholder,
/// never with its password.
#[tokio::test]
async fn a_hostless_or_opaque_url_is_never_shown_with_its_password() {
    let (_alive, state) = app(|_| {});
    let owner = state
        .sessions
        .create(true, CursorOptions::default())
        .unwrap();
    for (seq, url) in (1..).zip([
        "rtsp:////root:SECRET@cam.local/s",
        "rtsp:///root:SECRET@cam.local",
        "rtsp:root:SECRET@cam.local",
        "mailto:root:SECRET@cam.local",
    ]) {
        let add = command(json!({"type": "addItems", "open": false, "items": [
            {"type": "Live", "host": "cam.local", "url": url, "overlay": {"mode": "none"}}]}));
        assert!(is_ok(&answer(&state, &owner, seq, add).await), "{url}");
    }
    let doc = owner.state().doc.clone();
    let projected: DocWire = crate::session::project::project(&doc, Audience::HOST, false);
    let wire = serde_json::to_string(&projected).unwrap();
    assert!(
        !wire.contains("SECRET") && wire.contains("(unusable URL)"),
        "{wire}"
    );
    for item in &doc.workspace.parts().workspace.items {
        let text = format!("{:?} {:#?}", item.recipe.source, item);
        assert!(!text.contains("SECRET"), "{text}");
    }
}

/// A stream with no login can move to the web API; one with a login is asked for it again, as moving a login to
/// another address always is.
#[tokio::test]
async fn an_anonymous_stream_changes_route_but_one_with_a_login_is_asked_for_it_again() {
    let (_alive, state) = app(|settings| {
        for key in ["camera.user", "camera.password"] {
            settings.update(key, |_| Some(json!(""))).unwrap();
        }
    });
    let owner = state
        .sessions
        .create(true, CursorOptions::default())
        .unwrap();
    let add = |fields: serde_json::Value| {
        let mut item = json!({"type": "Live", "host": "cam.local", "overlay": {"mode": "none"},
            "kept_mqtt": kept()});
        item.as_object_mut()
            .unwrap()
            .extend(fields.as_object().unwrap().clone());
        command(json!({"type": "addItems", "open": false, "items": [item]}))
    };
    assert!(is_ok(&answer(&state, &owner, 1, add(json!({}))).await));
    assert!(is_ok(
        &answer(
            &state,
            &owner,
            2,
            add(json!({"username": "root", "password": "pw"}))
        )
        .await
    ));
    let ids: Vec<String> = owner
        .state()
        .doc
        .workspace
        .parts()
        .workspace
        .items
        .iter()
        .map(|item| item.id.to_string())
        .collect();
    let to_web_api = |id: &str| {
        command(json!({"type": "editItem", "item": id, "new": {
            "type": "Live", "host": "cam.local", "rtsp_route": "web_api",
            "overlay": {"mode": "none"}, "kept_mqtt": kept()}}))
    };
    let routes = || -> Vec<workspace::RtspRoute> {
        owner
            .state()
            .doc
            .workspace
            .parts()
            .workspace
            .items
            .iter()
            .map(|item| match &item.recipe.source {
                Source::Live(recipe) => recipe.rtsp_route,
                _ => panic!("a live stream"),
            })
            .collect()
    };
    assert!(is_ok(&answer(&state, &owner, 3, to_web_api(&ids[0])).await));
    let refused = answer(&state, &owner, 4, to_web_api(&ids[1])).await;
    assert!(matches!(
        refused,
        SessionServerMessage::Ack {
            outcome: AckOutcome::Refused { .. },
            ..
        }
    ));
    assert_eq!(
        routes(),
        [workspace::RtspRoute::WebApi, workspace::RtspRoute::Direct]
    );
}

/// One stream saved with `saved_url`, shown to clients as `shown`; every one of `spellings` is that URL as shown.
async fn scenario(saved_url: &str, shown: &str, spellings: &[&str]) {
    let (_alive, state) = app(|_| {});
    let owner = state
        .sessions
        .create(true, CursorOptions::default())
        .unwrap();
    let add = command(json!({"type": "addItems", "open": false, "items": [
        {"type": "Live", "host": "cam.local", "url": saved_url, "overlay": {"mode": "none"},
         "kept_mqtt": kept()}]}));
    assert!(is_ok(&answer(&state, &owner, 1, add).await));
    let id = owner.state().doc.workspace.parts().workspace.items[0]
        .id
        .to_string();
    let stored = || match &owner.state().doc.workspace.parts().workspace.items[0]
        .recipe
        .source
    {
        Source::Live(recipe) => recipe.url.as_deref().unwrap_or_default().to_owned(),
        _ => panic!("a live stream"),
    };
    assert_eq!(stored(), saved_url);
    let projected: DocWire =
        crate::session::project::project(&owner.state().doc, Audience::HOST, false);
    let wire = serde_json::to_string(&projected).unwrap();
    assert!(!wire.contains(PASSWORD), "{wire}");
    assert!(wire.contains(shown), "{wire}");

    let edit = |url: &str| {
        command(json!({"type": "editItem", "item": id, "new": {
            "type": "Live", "host": "cam.local", "url": url, "overlay": {"mode": "none"},
            "kept_mqtt": kept()}}))
    };
    // An Owner who sends the URL as shown keeps the saved password, in any spelling of it.
    for (seq, sent) in (10..).zip(spellings) {
        assert!(
            is_ok(&answer(&state, &owner, seq, edit(sent)).await),
            "{sent}"
        );
        assert_eq!(stored(), saved_url, "{sent}");
    }
}

/// A saved URL no connection can be made to is shown as a placeholder. The owner who renames the stream sends that
/// back and keeps the URL and every password; replacing it with a connectable URL keeps the camera's login.
#[tokio::test]
async fn an_unusable_saved_url_survives_a_rename_and_is_replaced_by_a_good_one_without_losing_the_login()
 {
    let (_alive, state) = app(|_| {});
    let owner = state
        .sessions
        .create(true, CursorOptions::default())
        .unwrap();
    let hostless = format!("rtsp:////root:{PASSWORD}@cam.local/s");
    let add = command(json!({"type": "addItems", "open": false, "items": [
        {"type": "Live", "host": "cam.local", "username": "root", "password": "camera-pw", "url": hostless,
         "overlay": {"mode": "none"}, "kept_mqtt": kept()}]}));
    assert!(is_ok(&answer(&state, &owner, 1, add).await));
    let item = || owner.state().doc.workspace.parts().workspace.items[0].clone();
    let id = item().id.to_string();
    let stored = || match item().recipe.source {
        Source::Live(recipe) => (
            recipe.url.as_deref().unwrap_or_default().to_owned(),
            recipe.password.expose().to_owned(),
        ),
        _ => panic!("a live stream"),
    };
    let projected: DocWire =
        crate::session::project::project(&owner.state().doc, Audience::HOST, false);
    let wire = serde_json::to_string(&projected).unwrap();
    assert!(
        wire.contains("(unusable URL)") && !wire.contains(PASSWORD),
        "{wire}"
    );
    let edit = |url: &str, label: &str| {
        command(json!({"type": "editItem", "item": id, "new": {
            "type": "Live", "host": "cam.local", "url": url, "label": label,
            "overlay": {"mode": "none"}, "kept_mqtt": kept()}}))
    };

    // A rename that sends the URL back as shown changes nothing else.
    assert!(is_ok(
        &answer(&state, &owner, 3, edit("(unusable URL)", "Gate")).await
    ));
    assert_eq!(item().label, "Gate");
    assert_eq!(stored(), (hostless, "camera-pw".to_owned()));
    // A connectable URL replaces it, and the camera's login stays.
    assert!(is_ok(
        &answer(&state, &owner, 4, edit("rtsp://cam.local/s", "Gate")).await
    ));
    assert_eq!(
        stored(),
        ("rtsp://cam.local/s".to_owned(), "camera-pw".to_owned())
    );
}
