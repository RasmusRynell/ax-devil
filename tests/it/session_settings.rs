//! The settings notice on the Session socket: told to an Owner's connection after its welcome, never to a Guest.

use crate::session_client::{Socket, patch_settings};
use crate::session_socket::{PASSWORD, command, of_type, serve, view};
use serde_json::json;

#[tokio::test]
async fn a_settings_change_is_announced_to_an_owner_connection_and_never_to_a_join_link() {
    let folder = tempfile::tempdir().unwrap();
    let address = serve(folder.path()).await;
    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    // An Owner is told the current revision at once, so a change made before it connected is not missed.
    // The page takes the first notice of a connection as the start of a new revision order, so it follows the welcome.
    let mut seen = Vec::new();
    let first = owner
        .until(|m| {
            seen.push(m["type"].clone());
            m["type"] == "settings"
        })
        .await["revision"]
        .as_u64()
        .unwrap();
    assert_eq!(seen.first(), Some(&json!("welcome")));
    owner
        .say(command(
            1,
            json!({"type": "control", "control": {"type": "share", "access": "watch"}}),
        ))
        .await;
    let seat = owner
        .until(|m| m["type"] == "seat" && !m["sharing"].is_null())
        .await;
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
        .until(|m| m["type"] == "settings" && m["revision"] != first)
        .await;
    assert_eq!(
        told,
        json!({"type": "settings", "revision": saved["revision"]})
    );

    // The guest's own view is answered after the change, and nothing about settings came before it.
    guest.say(view(1, false)).await;
    guest
        .until(|m| {
            assert_ne!(m["type"], "settings", "a guest was told");
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
    let session = creator.until(of_type("welcome")).await["session"].clone();
    creator
        .say(command(
            1,
            json!({"type": "control", "control": {"type": "share", "access": "watch"}}),
        ))
        .await;
    creator
        .until(|m| m["type"] == "seat" && !m["sharing"].is_null())
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
    let first = attached.until(of_type("settings")).await["revision"]
        .as_u64()
        .unwrap();
    let saved = patch_settings(
        &address,
        PASSWORD,
        &json!({"set": {"overlays.lane_names": false}}),
    )
    .await;
    let told = attached
        .until(|m| m["type"] == "settings" && m["revision"].as_u64() != Some(first))
        .await;
    assert_eq!(told["revision"], saved["revision"]);
}
