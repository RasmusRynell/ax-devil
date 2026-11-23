//! The `changed` notices on the Session socket: told to an Owner's connection after its welcome, never to a Guest.

use crate::session_client::{Socket, patch_settings};
use crate::session_socket::{PASSWORD, command, of_type, serve, view};
use serde_json::json;

/// The notice of `topic`.
fn topic(topic: &'static str) -> impl FnMut(&serde_json::Value) -> bool {
    move |m| m["type"] == "changed" && m["topic"] == topic
}

#[tokio::test]
async fn a_change_is_announced_to_an_owner_connection_and_never_to_a_join_link() {
    let folder = tempfile::tempdir().unwrap();
    let address = serve(folder.path()).await;
    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    // An Owner is told every topic's current revision at once, so a change made before it connected is not missed.
    let mut seen = Vec::new();
    let first = owner
        .until("initial settings change notice", |m| {
            seen.push(m["type"].clone());
            topic("settings")(m)
        })
        .await["revision"]
        .as_u64()
        .unwrap();
    assert_eq!(seen.first(), Some(&json!("welcome")));
    let listed = owner
        .until("initial sessions change notice", topic("sessions"))
        .await["revision"]
        .clone();
    owner
        .say(command(
            1,
            json!({"type": "control", "control": {"type": "share", "access": "watch"}}),
        ))
        .await;
    // Sharing also puts the session in the list other windows read.
    let (mut seat, mut relisted) = (None, false);
    owner
        .until("sharing seat and sessions change notice", |m| {
            if m["type"] == "seat" && !m["sharing"].is_null() {
                seat = Some(m.clone());
            }
            relisted |= topic("sessions")(m) && m["revision"] != listed;
            seat.is_some() && relisted
        })
        .await;
    let seat = seat.unwrap();
    let link = seat["sharing"]["link"].as_str().unwrap();
    let link = link.split("join=").nth(1).unwrap();
    let mut guest = Socket::connect(&address, "/api/session").await;
    guest
        .say(
            json!({"type": "hello", "auth": {"type": "join", "link": link},
                    "cursors": {"show": true, "share": true}}),
        )
        .await;

    let saved = patch_settings(
        &address,
        PASSWORD,
        &json!({"set": {"overlays.lane_names": false}}),
    )
    .await;
    let told = owner
        .until("settings change notice after save", |m| {
            topic("settings")(m) && m["revision"] != first
        })
        .await;
    assert_eq!(
        told,
        json!({"type": "changed", "topic": "settings", "revision": saved["revision"]})
    );

    // The guest's own view is answered after the changes, and no notice came before it.
    guest.say(view(1, false)).await;
    guest
        .until("guest seat without change notices", |m| {
            assert_ne!(m["type"], "changed", "a guest was told");
            m["type"] == "seat" && m["viewsDone"] == 1
        })
        .await;
}

#[tokio::test]
async fn an_owner_attached_as_a_guest_seat_is_still_told_of_a_settings_change() {
    let folder = tempfile::tempdir().unwrap();
    let on =
        json!({"version": settings::VERSION, "settings": {"server.password_full_access": true}});
    std::fs::write(folder.path().join("default.json"), on.to_string()).unwrap();
    let address = serve(folder.path()).await;
    // A Session created with Full access reads the server's environment, so an Owner without it attaches as a Guest, if it is shared.
    let mut creator = Socket::own(&address, Some(PASSWORD)).await;
    let session = creator.until("welcome message", of_type("welcome")).await["session"].clone();
    creator
        .say(command(
            1,
            json!({"type": "control", "control": {"type": "share", "access": "watch"}}),
        ))
        .await;
    creator
        .until("seat with sharing started", |m| {
            m["type"] == "seat" && !m["sharing"].is_null()
        })
        .await;
    patch_settings(
        &address,
        PASSWORD,
        &json!({"set": {"server.password_full_access": false}}),
    )
    .await;

    let mut attached = Socket::connect(&address, "/api/session").await;
    attached
        .say(json!({"type": "hello",
            "auth": {"type": "attach", "session": session, "serverPassword": PASSWORD},
            "cursors": {"show": true, "share": true}}))
        .await;
    let first = attached
        .until("initial settings change notice", topic("settings"))
        .await["revision"]
        .as_u64()
        .unwrap();
    let saved = patch_settings(
        &address,
        PASSWORD,
        &json!({"set": {"overlays.lane_names": false}}),
    )
    .await;
    let told = attached
        .until("settings change notice after save", |m| {
            topic("settings")(m) && m["revision"].as_u64() != Some(first)
        })
        .await;
    assert_eq!(told["revision"], saved["revision"]);
}
