use super::*;
use crate::{access::Policy, plugins::Plugins};
use serde_json::json;
use sessions::CursorOptions;
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
            .create(true, expands, CursorOptions::default())
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
