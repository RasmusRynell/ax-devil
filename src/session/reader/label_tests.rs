use super::*;
use crate::{access::Policy, plugins::Plugins};
use api_types::CursorPoint;
use serde_json::json;
use sessions::CursorOptions;
use sessions::session::{Control, Handle};
use std::sync::Arc;

fn command(command: serde_json::Value) -> api_types::SessionCommand {
    serde_json::from_value(json!({"type": "content", "command": command})).unwrap()
}

/// A label that is empty or exactly the item's current default name is stored empty (WSC-003), however it is set (a
/// new stream, an edit, a rename), and the default is read in the session's own environment: a session that expands
/// references reads `Live stream` for a `$PATH` host that is no plain host, one that does not reads the text as typed.
#[tokio::test]
async fn a_label_that_repeats_the_default_in_the_sessions_environment_is_stored_empty() {
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
    for expands in [true, false] {
        let session = state
            .sessions
            .create(expands, CursorOptions::default())
            .unwrap();
        let mut seq = 0;
        let mut send = |wire: serde_json::Value| {
            seq += 1;
            answer(&state, &session, seq, command(wire))
        };
        let labels = || -> Vec<String> {
            let doc = session.state().doc.clone();
            doc.workspace
                .parts()
                .workspace
                .items
                .iter()
                .map(|item| item.label.clone())
                .collect()
        };
        let ids = || -> Vec<String> {
            let doc = session.state().doc.clone();
            doc.workspace
                .parts()
                .workspace
                .items
                .iter()
                .map(|item| item.id.to_string())
                .collect()
        };
        let add = |host: &str, label: &str| {
            json!({"type": "addItems", "open": false,
                "items": [{"type": "Live", "host": host, "label": label}]})
        };
        let rename =
            |item: &str, label: &str| json!({"type": "renameItem", "item": item, "label": label});

        // The default of a live stream is its host as read: a literal one as typed, a reference by its value
        // (`Live stream` while that is no plain host), or as typed again where references are not expanded.
        send(add("cam.local", "cam.local")).await;
        send(add("$PATH", "Live stream")).await;
        send(add("$PATH", "Gate")).await;
        let now = labels();
        assert_eq!(now[0], "", "a literal host is its own default");
        assert_eq!(now[1], if expands { "" } else { "Live stream" });
        assert_eq!(now[2], "Gate");

        send(rename(&ids()[2], "$PATH")).await;
        assert_eq!(labels()[2], if expands { "$PATH" } else { "" });
        send(rename(&ids()[2], "Live stream")).await;
        assert_eq!(labels()[2], if expands { "" } else { "Live stream" });
        send(rename(&ids()[0], "Cam")).await;
        send(rename(&ids()[0], "  cam.local ")).await;
        assert_eq!(labels()[0], "");

        // An edit that names the stream by its default clears the label too.
        let id = ids()[0].clone();
        let edit = json!({"type": "editItem", "item": id, "new": {
            "type": "Live", "host": "cam.local", "label": "cam.local",
            "overlay": {"mode": "datahub", "handler": "ADF_V1_FRAME", "topic": "t", "channel": 1}}});
        send(rename(&id, "Mine")).await;
        send(edit).await;
        assert_eq!(labels()[0], "", "expands: {expands}");
    }
}

fn app(folder: &std::path::Path) -> AppState {
    let settings = settings::Settings::load(&folder.join("default.json")).unwrap();
    let cache = folder.join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
    AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    )
}

fn items_of(handle: &Handle<CursorPoint>) -> Vec<(String, String)> {
    let doc = handle.state().doc.clone();
    doc.workspace
        .parts()
        .workspace
        .items
        .iter()
        .map(|item| (item.id.to_string(), item.label.clone()))
        .collect()
}

/// A seat that is not shown the host (an Edit guest) is answered nothing about it by what clears: a label equal to
/// the host stays, and only the placeholder it is shown clears. The owner's own behaviour is the other test's.
#[tokio::test]
async fn a_guest_cannot_find_a_hosts_text_by_which_label_clears() {
    let folder = tempfile::tempdir().unwrap();
    let state = app(folder.path());
    let owner = state
        .sessions
        .create(true, CursorOptions::default())
        .unwrap();
    answer(
        &state,
        &owner,
        1,
        command(json!({"type": "addItems", "open": false,
            "items": [{"type": "Live", "host": "cam.local"}]})),
    )
    .await;
    owner
        .command(sessions::session::Command::Control(Control::Share {
            access: sessions::session::Access::Edit,
        }))
        .unwrap();
    let link = owner.state().sharing.as_ref().unwrap().link.clone();
    let guest = state
        .sessions
        .join(&link, CursorOptions::default())
        .unwrap();
    let id = items_of(&owner)[0].0.clone();
    let rename = |label: &str| json!({"type": "renameItem", "item": id, "label": label});
    answer(&state, &guest, 1, command(rename("cam.local"))).await;
    assert_eq!(items_of(&owner)[0].1, "cam.local", "no oracle for the host");
    answer(&state, &guest, 2, command(rename("Live stream"))).await;
    assert_eq!(
        items_of(&owner)[0].1,
        "",
        "the placeholder it is shown clears"
    );
    // The owner is shown the host, so naming the stream by it names nothing.
    answer(&state, &owner, 2, command(rename("cam.local"))).await;
    assert_eq!(items_of(&owner)[0].1, "");

    // A guest adds nothing, so no add is a route for the question.
    let add = json!({"type": "addItems", "open": false,
        "items": [{"type": "Live", "host": "cam.local", "label": "cam.local"}]});
    answer(&state, &guest, 3, command(add)).await;
    assert_eq!(items_of(&owner).len(), 1);
}

/// Every route that names an item clears a label equal to its default, for an owner whose references expand and one
/// whose do not: adding a video, adding a staged item, and editing with one.
#[tokio::test]
async fn adding_and_editing_a_video_or_a_staged_item_clears_a_default_label() {
    let folder = tempfile::tempdir().unwrap();
    let clip = folder.path().join("clip.mp4");
    std::fs::write(&clip, b"not really a video").unwrap();
    let state = app(folder.path());
    let handlers = handlers::Handlers::built_in();
    for expands in [true, false] {
        let owner = state
            .sessions
            .create(expands, CursorOptions::default())
            .unwrap();
        let session = owner.session().to_string();
        let video = json!({"type": "Video", "path": clip.to_str().unwrap()});
        let with_label = |label: &str| {
            let mut video = video.clone();
            video["label"] = json!(label);
            command(json!({"type": "addItems", "open": false, "items": [video]}))
        };
        answer(&state, &owner, 1, with_label("clip.mp4")).await;
        answer(&state, &owner, 2, with_label("Mine")).await;
        let labels: Vec<String> = items_of(&owner).into_iter().map(|(_, l)| l).collect();
        assert_eq!(labels, ["", "Mine"], "expands: {expands}");

        // A staged item, added or replacing one, is named the same way.
        let stage = || {
            let item = workspace::intake::video(&clip, &[], &handlers).unwrap();
            let content = item
                .resolve(
                    &handlers,
                    &workspace::Environment::literal(),
                    &Default::default(),
                )
                .ok()
                .unwrap();
            let id = item.id;
            state.staged.put(&session, item, content, None, |_| true);
            id.to_string()
        };
        let added = command(json!({"type": "addResolved", "item": stage(), "label": "clip.mp4"}));
        answer(&state, &owner, 3, added).await;
        let target = items_of(&owner)[1].0.clone();
        let edited = command(
            json!({"type": "editResolved", "item": target, "ticket": stage(), "label": "clip.mp4"}),
        );
        answer(&state, &owner, 4, edited).await;
        let labels: Vec<String> = items_of(&owner).into_iter().map(|(_, l)| l).collect();
        assert_eq!(labels.len(), 3, "expands: {expands}");
        assert!(
            labels.iter().all(String::is_empty),
            "{labels:?} (expands: {expands})"
        );
    }
}
