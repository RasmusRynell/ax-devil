//! The Session socket over real WebSockets: a window's session works alone, sharing it admits a Guest who learns
//! only what a Guest may, and stopping ends the Guest and its video.

use crate::session_client;

use api_types::playback::PLAYBACK_PROTOCOL;
use ax_devil::{
    AppState,
    access::Policy,
    app,
    session::{Launch, LaunchQueue},
    share::{Hosting, LinkBase},
};
use serde_json::{Value, json};
use session_client::{Proxy, Socket, get};
use sessions::Secret;
use std::{net::SocketAddr, path::Path, sync::Arc};

const PASSWORD: &str = "pw";

/// A server with a password, whose links point at itself, like `serve`. Returns its address.
async fn serve(folder: &Path) -> String {
    serve_with(folder, None).await
}

/// As [`serve`], with `launch` waiting for the first Local session.
async fn serve_with(folder: &Path, launch: Option<Launch>) -> String {
    let settings = settings::Settings::load(&folder.join("default.json")).unwrap();
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let cache = folder.join("cache");
    let videos = ax_devil::video_cache::start(&settings, &cache);
    // Kept by the server task, so the backend never sees its stop signal.
    let (stop, stopping) = tokio::sync::watch::channel(false);
    let settings = Arc::new(settings);
    let policy = Policy::Shared {
        password: Some(Secret::chosen(PASSWORD.into()).unwrap()),
        settings: Arc::clone(&settings),
    };
    let state = AppState::new(
        settings,
        policy,
        videos,
        stopping,
        ax_devil::plugins::Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    )
    .with_launch(Arc::new(LaunchQueue::new(launch)))
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

/// A view with the sidebar `open` and viewer 1 showing `item`.
fn viewing(seq: u64, open: bool, item: &Value) -> Value {
    json!({"type": "view", "seq": seq, "view": {
        "sidebar": {"view": "live", "open": open}, "active": 1,
        "viewers": {"1": {"item": item}},
        "layout": {"type": "pane", "viewer": 1}}})
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
    let listed = get(&address, "/api/sessions", PASSWORD).await;
    assert_eq!(
        listed[0]["link"],
        json!(null),
        "a private session has no link"
    );

    // Two videos with a viewer each: one that plays and one that is missing, whose failure names a host path.
    let add = json!({"type": "content", "command": {"type": "addItems", "open": true, "items": [
        {"type": "Video", "path": video.display().to_string(), "label": " Clip "},
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
    assert_eq!(items[0]["label"], "Clip", "the name the user gave");
    let content = items
        .iter()
        .find_map(|item| item["resolution"]["content"].as_object())
        .unwrap();
    let key = json!({
        "content": content["id"],
        "generation": content["generation"],
        "entry": content["entry"]["id"],
        "viewer": 1,
    });
    owner.say(viewing(2, false, &items[0]["id"])).await;
    // A failed add opens nothing; the viewer of the missing video is asked for, so Guests see how it failed.
    let lost = items[1]["id"].clone();
    let open_lost = json!({"type": "content", "command": {"type": "openViewer", "item": lost}});
    owner.say(command(2, open_lost)).await;
    owner
        .until(|m| {
            m["doc"]["workspace"]["items"]
                .as_array()
                .is_some_and(|items| {
                    items.iter().any(|item| {
                        item["id"] == lost
                            && item["open"] == true
                            && item["resolution"]["status"] == "Failed"
                    })
                })
        })
        .await;

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

    // The Guest sees the Doc without the host's path or reasons, and cannot change anything.
    let mut guest = Socket::connect(&address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    guest.say(hello).await;
    let doc = guest.settled_doc().await;
    let seen = doc["doc"].to_string();
    for host_only in [
        "SECRET-DIR",
        "reason",
        // A row's `location` is null for a Guest.
        "\"location\":\"",
        folder.path().to_str().unwrap(),
    ] {
        assert!(
            !seen.contains(host_only),
            "{host_only} reached the Guest: {seen}"
        );
    }
    assert!(
        seen.contains("FileNotFound"),
        "the Guest still learns that it failed: {seen}"
    );
    guest.say(view(1, false)).await;
    let seat = guest
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 1)
        .await;
    assert_eq!(seat["access"], json!("watch"));
    assert_eq!(
        seat["view"]["sidebar"]["open"],
        json!(false),
        "it follows the presenter, who closed it"
    );
    guest
        .say(command(
            1,
            json!({"type": "content", "command": {"type": "closeViewer", "item": items[0]["id"]}}),
        ))
        .await;
    let ack = guest.until(of_type("ack")).await;
    assert_eq!(
        ack["outcome"],
        json!({"status": "refused", "code": "NotAllowed"})
    );

    // It may stream what is open, and the stream ends with the sharing.
    guest
        .say(command(2, json!({"type": "requestTicket", "key": key})))
        .await;
    let ticket = guest.until(of_type("ticket")).await["ticket"].clone();
    let mut playback = Socket::connect(&address, "/api/playback").await;
    playback
        .say(json!({"type": "Open", "ticket": ticket, "data_bytes": 1 << 20}))
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
    let ended = guest.until(of_type("ended")).await;
    assert_eq!(ended["reason"], json!("stopped"));
    assert_eq!(guest.closed().await, 4404);
    assert_eq!(playback.closed().await, 4403);

    // The owner and its session go on, solo again, and the old link is dead.
    owner.say(view(3, true)).await;
    owner
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 3 && m["sharing"].is_null())
        .await;
    let listed = get(&address, "/api/sessions", PASSWORD).await;
    assert_eq!(
        listed[0]["link"],
        json!(null),
        "a private session has no link"
    );
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

    let mut guest = Socket::connect(&address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    guest.say(hello).await;
    // Sent in one burst: the second view waits for the lane's rate, and the follow arrives while it does.
    guest.say(view(1, false)).await;
    guest.say(view(2, false)).await;
    guest.say(command(1, json!({"type": "follow"}))).await;

    let seat = guest
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 2)
        .await;
    assert_eq!(seat["mode"], json!("following"));
}

#[tokio::test]
async fn a_seat_without_the_files_grant_is_refused_alike_whatever_it_asks_to_add() {
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
    let mut editor = Socket::connect(&address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    editor.say(hello).await;

    // A resolved item nobody staged, and a file that does not exist: none shows through the refusal.
    let unstaged = json!({"type": "addResolved", "item": workspace::ItemId::generate().to_string(), "label": null});
    let missing =
        json!({"type": "Video", "path": folder.path().join("lost.mp4").display().to_string()});
    let add_video = json!({"type": "addItems", "open": false, "items": [missing]});
    for (seq, doc) in [unstaged, add_video].into_iter().enumerate() {
        let add = json!({"type": "content", "command": doc});
        editor.say(command(seq as u64 + 1, add)).await;
        let ack = editor.until(of_type("ack")).await;
        assert_eq!(
            ack["outcome"],
            json!({"status": "refused", "code": "NotAllowed"}),
            "{doc}"
        );
    }
}

/// Opens a Session socket through `address` that takes the seat of `resume` back.
async fn resumed(address: &str, resume: &str) -> Socket {
    let mut socket = Socket::connect(address, "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "own", "serverPassword": null},
                       "resume": resume, "cursors": {"show": true, "share": true}});
    socket.say(hello).await;
    socket
}

#[tokio::test]
async fn a_watcher_whose_network_is_cut_takes_its_seat_back_but_not_after_the_sharing_stopped() {
    let folder = tempfile::tempdir().unwrap();
    let address = serve(folder.path()).await;
    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    owner.until(of_type("seat")).await;
    let share = json!({"type": "control", "control": {"type": "share", "access": "watch"}});
    owner.say(command(1, share)).await;
    let seat = owner
        .until(|m| m["type"] == "seat" && !m["sharing"].is_null())
        .await;
    let link = seat["sharing"]["link"].as_str().unwrap();
    let link = link.split("join=").nth(1).unwrap();

    // The Guest reaches the backend through a proxy that can be cut.
    let proxy = Proxy::to(&address).await;
    let mut guest = Socket::connect(proxy.address(), "/api/session").await;
    let hello = json!({"type": "hello", "auth": {"type": "join", "link": link},
                       "cursors": {"show": true, "share": true}});
    guest.say(hello).await;
    let welcome = guest.until(of_type("welcome")).await;
    let (seat_id, token) = (
        welcome["seat"].clone(),
        welcome["resume"].as_str().unwrap().to_owned(),
    );

    // The connection drops while the presenter moves on; the Guest resumes the same seat and catches up.
    proxy.cut();
    assert_eq!(guest.closed().await, 0, "cut, not closed");
    owner.say(view(1, true)).await;
    owner
        .until(|m| m["type"] == "seat" && m["viewsDone"] == 1)
        .await;
    let mut guest = resumed(proxy.address(), &token).await;
    let welcome = guest.until(of_type("welcome")).await;
    assert_eq!(welcome["seat"], seat_id, "the same seat");
    let again = welcome["resume"].as_str().unwrap().to_owned();
    assert_ne!(again, token, "each welcome brings a new token");
    let seat = guest.until(of_type("seat")).await;
    assert_eq!(
        seat["view"]["sidebar"]["open"],
        json!(true),
        "the presenter's view"
    );

    // A token is used once, and a stopped sharing takes the seat with it, cut off or not.
    let mut stale = resumed(proxy.address(), &token).await;
    assert_eq!(stale.closed().await, 4404);
    proxy.cut();
    assert_eq!(guest.closed().await, 0);
    let stop = json!({"type": "control", "control": {"type": "stop"}});
    owner.say(command(2, stop)).await;
    owner
        .until(|m| m["type"] == "seat" && m["sharing"].is_null())
        .await;
    let mut late = resumed(proxy.address(), &again).await;
    assert_eq!(late.closed().await, 4404);
}

#[tokio::test]
async fn a_password_holder_with_full_access_does_not_take_the_launch_meant_for_the_local_listener()
{
    let folder = tempfile::tempdir().unwrap();
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4");
    let item = workspace::intake::video(&video, &[], &handlers::Handlers::built_in()).unwrap();
    let on =
        json!({"version": settings::VERSION, "settings": {"server.password_full_access": true}});
    std::fs::write(folder.path().join("default.json"), on.to_string()).unwrap();
    let address = serve_with(folder.path(), Some(Launch::Items(vec![item]))).await;

    let mut owner = Socket::own(&address, Some(PASSWORD)).await;
    let doc = owner.until(of_type("doc")).await;
    assert_eq!(doc["doc"]["workspace"]["items"], json!([]));
}
