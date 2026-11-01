//! The Session socket over real WebSockets: a window's session works alone, sharing it admits a watcher who learns
//! only what a watcher may, and stopping ends the watcher and its video.

mod session_client;

use api_types::playback::PLAYBACK_PROTOCOL;
use ax_devil::{
    AppState,
    access::Policy,
    app,
    share::{Hosting, LinkBase},
};
use serde_json::{Value, json};
use session_client::{Socket, get};
use sessions::Secret;
use std::{net::SocketAddr, path::Path, sync::Arc};

const PASSWORD: &str = "pw";

/// A server with a password, whose links point at itself, like `serve`. Returns its address.
async fn serve(folder: &Path) -> String {
    let settings = settings::Settings::load(&folder.join("default.json")).unwrap();
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let videos = ax_devil::video_cache::start(&settings, &folder.join("cache"));
    // Kept by the server task, so the backend never sees its stop signal.
    let (stop, stopping) = tokio::sync::watch::channel(false);
    let policy = Policy::Shared {
        password: Some(Secret::chosen(PASSWORD.into()).unwrap()),
    };
    let state = AppState::new(Arc::new(settings), policy, videos, stopping)
        .with_hosting(Hosting::Server(LinkBase::for_listener(None, address)));
    tokio::spawn(async move {
        let service = app(state).into_make_service_with_connect_info::<SocketAddr>();
        axum::serve(listener, service).await.unwrap();
        drop(stop);
    });
    address.to_string()
}

fn of_type(kind: &'static str) -> impl Fn(&Value) -> bool {
    move |message| message["type"] == kind
}

fn command(seq: u64, cmd: Value) -> Value {
    json!({"type": "command", "seq": seq, "cmd": cmd})
}

fn view(seq: u64, open: bool) -> Value {
    json!({"type": "view", "seq": seq, "view": {
        "sidebar": {"view": "live", "open": open}, "viewers": {}}})
}

#[tokio::test]
async fn a_session_works_alone_then_a_watcher_joins_it_sees_only_what_it_may_and_stopping_ends_it()
{
    let folder = tempfile::tempdir().unwrap();
    let address = serve(folder.path()).await;
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4");
    let secret_folder = folder.path().join("SECRET-DIR");

    // Alone: the owner has a session from its first connect, and the list shows none until it is shared.
    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    let welcome = owner.until(of_type("welcome")).await;
    let session = welcome["session"].as_str().unwrap().to_owned();
    let seat = owner.until(of_type("seat")).await;
    assert_eq!(
        (&seat["access"], seat["sharing"].is_null()),
        (&json!("owner"), true)
    );
    owner.say(view(1, false)).await;
    let seat = owner
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 1)
        .await;
    assert_eq!(
        seat["view"]["sidebar"],
        json!({"view": "live", "open": false})
    );
    assert_eq!(get(&address, "/api/sessions", PASSWORD).await, json!([]));

    // Two videos with a viewer each: one that plays and one that is missing, whose failure names a host path.
    let add = json!({"type": "content", "command": {"type": "addItems", "open": true, "items": [
        {"type": "Video", "path": video.display().to_string()},
        {"type": "Video", "path": secret_folder.join("lost.mp4").display().to_string()},
    ]}});
    owner.say(command(1, add)).await;
    let doc = owner.settled_doc().await;
    let owners_doc = doc["doc"].to_string();
    assert!(
        owners_doc.contains("SECRET-DIR"),
        "the owner sees where it failed: {owners_doc}"
    );
    let items = doc["doc"]["workspace"]["items"].as_array().unwrap();
    let content = items
        .iter()
        .find_map(|item| item["resolution"]["content"].as_object())
        .unwrap();
    let key = json!({"content": content["id"], "generation": content["generation"]});

    // Sharing: a link appears for the owner, and the session is listed.
    owner
        .say(command(
            2,
            json!({"type": "control", "control": {"type": "share", "access": "watch"}}),
        ))
        .await;
    let seat = owner
        .until(|m| m["type"] == "seat" && !m["sharing"].is_null())
        .await;
    let link = seat["sharing"]["link"]
        .as_str()
        .unwrap()
        .split("join=")
        .nth(1)
        .unwrap()
        .to_owned();
    let listed = get(&address, "/api/sessions", PASSWORD).await;
    assert_eq!(listed[0]["id"], json!(session));

    // The watcher sees the Doc without the host's path or reasons, and cannot change anything.
    let mut watcher = Socket::connect(&address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    watcher.say(hello).await;
    let doc = watcher.settled_doc().await;
    let seen = doc["doc"].to_string();
    for host_only in [
        "SECRET-DIR",
        "reason",
        "location",
        folder.path().to_str().unwrap(),
    ] {
        assert!(
            !seen.contains(host_only),
            "{host_only} reached the watcher: {seen}"
        );
    }
    assert!(
        seen.contains("FileNotFound"),
        "the watcher still learns that it failed: {seen}"
    );
    watcher.say(view(1, false)).await;
    let seat = watcher
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 1)
        .await;
    assert_eq!(seat["access"], json!("watch"));
    assert_eq!(
        seat["view"]["sidebar"]["open"],
        json!(false),
        "it follows the presenter, who closed it"
    );
    watcher
        .say(command(
            1,
            json!({"type": "content", "command": {"type": "closeViewer", "item": items[0]["id"]}}),
        ))
        .await;
    let ack = watcher.until(of_type("ack")).await;
    assert_eq!(
        ack["outcome"],
        json!({"status": "refused", "code": "NotAllowed"})
    );

    // It may stream what is open, and the stream ends with the sharing.
    watcher
        .say(command(2, json!({"type": "requestTicket", "key": key})))
        .await;
    let ticket = watcher.until(of_type("ticket")).await["ticket"].clone();
    let mut playback = Socket::connect(&address, "/api/playback").await;
    playback
        .say(json!({"type": "Open", "ticket": ticket}))
        .await;
    let opened = playback.until(of_type("Opened")).await;
    assert_eq!(opened["protocol"], json!(PLAYBACK_PROTOCOL));
    playback.until_binary(|bytes| bytes[5] == 1).await;

    owner
        .say(command(
            3,
            json!({"type": "control", "control": {"type": "stop"}}),
        ))
        .await;
    let ended = watcher.until(of_type("ended")).await;
    assert_eq!(ended["reason"], json!("stopped"));
    assert_eq!(watcher.closed().await, 4404);
    assert_eq!(playback.closed().await, 4403);

    // The owner and its session go on, solo again, and the old link is dead.
    owner.say(view(2, true)).await;
    owner
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 2 && m["sharing"].is_null())
        .await;
    assert_eq!(get(&address, "/api/sessions", PASSWORD).await, json!([]));
    let mut late = Socket::connect(&address, "/api/session").await;
    late.say(
        json!({"type": "hello", "auth": {"type": "join", "link": link},
                    "cursors": {"show": true, "share": true}}),
    )
    .await;
    assert_eq!(late.closed().await, 4401);
}

#[tokio::test]
async fn a_follow_sent_after_a_waiting_view_is_not_undone_by_it() {
    let folder = tempfile::tempdir().unwrap();
    let address = serve(folder.path()).await;
    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    owner.until(of_type("seat")).await;
    let share = json!({"type": "control", "control": {"type": "share", "access": "explore"}});
    owner.say(command(1, share)).await;
    let seat = owner
        .until(|m| m["type"] == "seat" && !m["sharing"].is_null())
        .await;
    let link = seat["sharing"]["link"].as_str().unwrap();
    let link = link.split("join=").nth(1).unwrap();

    let mut watcher = Socket::connect(&address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    watcher.say(hello).await;
    // Sent in one burst: the second view waits for the lane's rate, and the follow arrives while it does.
    watcher.say(view(1, false)).await;
    watcher.say(view(2, false)).await;
    watcher.say(command(1, json!({"type": "follow"}))).await;

    let seat = watcher
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 2)
        .await;
    assert_eq!(seat["mode"], json!("following"));
}
