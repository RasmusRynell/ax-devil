//! The share socket over real WebSockets: who may be seated, what each role may send, and how a connection ends.

use ax_devil::{
    AppState, ShareConfig,
    access::Policy,
    app,
    share::{Hosting, LinkBase, ShareListener},
};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use sessions::Secret;
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::{io::AsyncReadExt, io::AsyncWriteExt, net::TcpStream, time::timeout};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async, tungstenite::Message};

type Client = WebSocketStream<MaybeTlsStream<TcpStream>>;

struct Server {
    address: SocketAddr,
    _folder: tempfile::TempDir,
    /// Keeps the backend from seeing its stop signal.
    _stop: tokio::sync::watch::Sender<bool>,
}

/// Starts a backend on a free port; `hosting` says where its join links point.
async fn start(
    policy: Policy,
    config: ShareConfig,
    hosting: impl FnOnce(&AppState, SocketAddr) -> Hosting,
) -> Server {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let videos = ax_devil::video_cache::start(&settings, &folder.path().join("cache"));
    let (stop, stopping) = tokio::sync::watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        policy,
        workspace_store::Store::new(tokio::runtime::Handle::current()),
        videos,
        stopping,
    )
    .with_share(config);
    let hosting = hosting(&state, address);
    let state = state.with_hosting(hosting);
    tokio::spawn(async move {
        let service = app(state).into_make_service_with_connect_info::<SocketAddr>();
        axum::serve(listener, service).await.unwrap();
    });
    Server {
        address,
        _folder: folder,
        _stop: stop,
    }
}

/// A server with a password, whose links point at itself, like `serve`.
async fn serve() -> Server {
    let policy = Policy::Shared {
        password: Some(Secret::chosen("pw".into())),
    };
    start(policy, ShareConfig::default(), |_, address| {
        Hosting::Server(LinkBase::for_listener(None, address))
    })
    .await
}

async fn connect(address: SocketAddr) -> Client {
    connect_async(format!("ws://{address}/api/share/socket"))
        .await
        .unwrap()
        .0
}

async fn say(client: &mut Client, message: Value) {
    client
        .send(Message::text(message.to_string()))
        .await
        .unwrap();
}

fn hello(access: Value, password: Option<&str>) -> Value {
    json!({"type": "hello", "access": access, "serverPassword": password,
           "cursors": {"show": true, "share": true}})
}

/// The next message that satisfies `wanted`, skipping heartbeats and the rest.
async fn until(client: &mut Client, mut wanted: impl FnMut(&Value) -> bool) -> Value {
    loop {
        let message = timeout(Duration::from_secs(5), client.next())
            .await
            .expect("no message within 5 seconds")
            .expect("the socket closed")
            .expect("the socket failed");
        if let Message::Text(text) = message {
            let message: Value = serde_json::from_str(&text).unwrap();
            if wanted(&message) {
                return message;
            }
        }
    }
}

fn of_type(kind: &'static str) -> impl Fn(&Value) -> bool {
    move |message| message["type"] == kind
}

/// The close code the server ends the socket with; 0 when it just hung up.
async fn closed(client: &mut Client) -> u16 {
    loop {
        match timeout(Duration::from_secs(5), client.next())
            .await
            .expect("the socket stayed open")
        {
            Some(Ok(Message::Close(frame))) => {
                return frame.map_or(1005, |frame| frame.code.into());
            }
            Some(Ok(_)) => {}
            Some(Err(_)) | None => return 0,
        }
    }
}

/// Opens a session as the password holder; returns the owner's socket, its session id and its join link secret.
async fn create(server: &Server) -> (Client, String, String) {
    let mut owner = connect(server.address).await;
    say(&mut owner, hello(json!({"type": "create"}), Some("pw"))).await;
    let welcome = until(&mut owner, of_type("welcome")).await;
    assert_eq!(welcome["role"], "owner");
    let link = welcome["link"].as_str().unwrap();
    let secret = link
        .split("join=")
        .nth(1)
        .unwrap()
        .split('.')
        .nth(1)
        .unwrap()
        .to_owned();
    (
        owner,
        welcome["session"].as_str().unwrap().to_owned(),
        secret,
    )
}

async fn join(server: &Server, link: &str) -> Client {
    let mut client = connect(server.address).await;
    say(
        &mut client,
        hello(json!({"type": "join", "link": link}), None),
    )
    .await;
    client
}

#[tokio::test]
async fn a_watcher_cannot_change_what_the_session_shows() {
    let server = serve().await;
    let (mut owner, id, secret) = create(&server).await;
    let link = format!("{id}.{secret}");

    let mut watcher = join(&server, &link).await;
    let welcome = until(&mut watcher, of_type("welcome")).await;
    assert_eq!(
        (&welcome["role"], &welcome["link"]),
        (&json!("watcher"), &Value::Null)
    );

    for forbidden in [
        json!({"type": "publish", "state": {"sidebarView": "live", "sidebarOpen": false}}),
        json!({"type": "setOptions", "explore": true}),
        json!({"type": "regenerateLink"}),
    ] {
        let mut watcher = join(&server, &link).await;
        say(&mut watcher, forbidden).await;
        assert_eq!(closed(&mut watcher).await, 1008);
    }

    // Nothing the watchers tried reached the session, while the owner still can.
    let seen = until(&mut watcher, |m| m["type"] == "snapshot").await;
    assert_eq!(
        seen["presenter"],
        json!({"sidebarView": "workspace", "sidebarOpen": true})
    );
    assert_eq!(seen["options"]["explore"], false);
    say(
        &mut owner,
        json!({"type": "publish", "state": {"sidebarView": "live", "sidebarOpen": false}}),
    )
    .await;
    let seen = until(&mut watcher, |m| m["presenter"]["sidebarView"] == "live").await;
    assert_eq!(seen["view"], seen["presenter"]);
}

#[tokio::test]
async fn a_link_reaches_only_its_own_session() {
    let server = serve().await;
    let (_owner_a, id_a, secret_a) = create(&server).await;
    let (_owner_b, id_b, _secret_b) = create(&server).await;

    // Another session by id needs the password; A's secret is no key to B.
    for (access, password, code) in [
        (json!({"type": "watch", "session": id_b}), None, 1008),
        (
            json!({"type": "watch", "session": id_b}),
            Some("guess"),
            4401,
        ),
        (json!({"type": "create"}), None, 1008),
        (
            json!({"type": "join", "link": format!("{id_b}.{secret_a}")}),
            None,
            4401,
        ),
        (json!({"type": "join", "link": "nonsense"}), None, 4401),
        (
            json!({"type": "watch", "session": "0000000000000000"}),
            Some("pw"),
            4404,
        ),
    ] {
        let mut client = connect(server.address).await;
        say(&mut client, hello(access.clone(), password)).await;
        assert_eq!(closed(&mut client).await, code, "{access}");
    }
    let mut own = join(&server, &format!("{id_a}.{secret_a}")).await;
    assert_eq!(until(&mut own, of_type("welcome")).await["session"], id_a);
    let mut by_password = connect(server.address).await;
    say(
        &mut by_password,
        hello(json!({"type": "watch", "session": id_b}), Some("pw")),
    )
    .await;
    let welcome = until(&mut by_password, of_type("welcome")).await;
    assert_eq!(welcome["session"], id_b);
    assert!(welcome["link"].is_string());
}

#[tokio::test]
async fn a_seat_resumes_once_and_its_older_socket_is_replaced() {
    let server = serve().await;
    let mut owner = connect(server.address).await;
    say(&mut owner, hello(json!({"type": "create"}), Some("pw"))).await;
    let welcome = until(&mut owner, of_type("welcome")).await;
    let token = welcome["resume"].as_str().unwrap().to_owned();

    let resume = |token: &str| hello(json!({"type": "resume", "token": token}), None);
    let mut newer = connect(server.address).await;
    say(&mut newer, resume(&token)).await;
    let resumed = until(&mut newer, of_type("welcome")).await;
    assert_eq!(
        (&resumed["role"], &resumed["you"]),
        (&json!("owner"), &welcome["you"])
    );
    assert_ne!(resumed["resume"], welcome["resume"]);
    assert!(resumed["link"].is_string());
    until(&mut owner, |m| m["reason"] == "replaced").await;
    assert_eq!(closed(&mut owner).await, 4409);

    // The old token died with the resume. A dropped socket can still be resumed within the grace period.
    let mut stale = connect(server.address).await;
    say(&mut stale, resume(&token)).await;
    assert_eq!(closed(&mut stale).await, 4404);
    let token = resumed["resume"].as_str().unwrap().to_owned();
    drop(newer);
    let mut again = connect(server.address).await;
    say(&mut again, resume(&token)).await;
    let back = until(&mut again, of_type("welcome")).await;
    assert_eq!(back["you"], welcome["you"]);
}

#[tokio::test]
async fn cursor_choices_sent_when_resuming_replace_the_seats_old_ones() {
    let server = serve().await;
    let (mut owner, id, secret) = create(&server).await;
    let link = format!("{id}.{secret}");
    let mut watcher = join(&server, &link).await;
    let welcome = until(&mut watcher, of_type("welcome")).await;
    let token = welcome["resume"].as_str().unwrap().to_owned();

    // The seat joined showing cursors; after the reload it asks not to.
    let mut hidden = connect(server.address).await;
    let mut resume = hello(json!({"type": "resume", "token": token}), None);
    resume["cursors"] = json!({"show": false, "share": true});
    say(&mut hidden, resume).await;
    until(&mut hidden, of_type("welcome")).await;
    let mut shown = join(&server, &link).await;
    until(&mut shown, of_type("snapshot")).await;

    let at = json!({"region": "main", "scope": "welcome", "x": 0.5, "y": 0.5});
    say(&mut owner, json!({"type": "cursor", "at": at})).await;
    until(&mut shown, of_type("cursor")).await;
    let cursor = timeout(
        Duration::from_millis(300),
        until(&mut hidden, of_type("cursor")),
    )
    .await;
    assert!(cursor.is_err(), "a seat that hides cursors received one");
}

#[tokio::test]
async fn stopping_tells_every_watcher_and_kills_the_link() {
    let server = serve().await;
    let (mut owner, id, secret) = create(&server).await;
    let link = format!("{id}.{secret}");
    let mut watchers = vec![join(&server, &link).await, join(&server, &link).await];
    for watcher in &mut watchers {
        until(watcher, of_type("welcome")).await;
    }
    say(&mut owner, json!({"type": "leave"})).await;
    for watcher in &mut watchers {
        until(watcher, |m| {
            m["type"] == "ended" && m["reason"] == "stopped"
        })
        .await;
        assert_eq!(closed(watcher).await, 4404);
    }
    assert_eq!(closed(&mut owner).await, 1000);
    let mut late = join(&server, &link).await;
    assert_eq!(closed(&mut late).await, 4404);
}

#[tokio::test]
async fn a_silent_seat_counts_as_disconnected() {
    let config = ShareConfig {
        heartbeat_interval: Duration::from_millis(50),
        ping_interval: Duration::from_millis(50),
        silence_timeout: Duration::from_millis(400),
        ..ShareConfig::default()
    };
    let server = start(Policy::Local, config, |_, _| Hosting::None).await;
    let mut owner = connect(server.address).await;
    say(&mut owner, hello(json!({"type": "create"}), None)).await;
    let welcome = until(&mut owner, of_type("welcome")).await;
    until(&mut owner, of_type("heartbeat")).await;
    let mut watcher = connect(server.address).await;
    let access = json!({"type": "watch", "session": welcome["session"]});
    say(&mut watcher, hello(access, None)).await;

    // The owner stops reading, so it neither answers pings nor says anything; the watcher keeps answering.
    let seen = until(&mut watcher, |m| {
        m["type"] == "snapshot" && m["ownerPresent"] == false
    })
    .await;
    assert_eq!(seen["participants"][0]["present"], false);
}

#[tokio::test]
async fn a_seat_that_sends_too_much_or_too_big_is_cut_off() {
    let server = start(Policy::Local, ShareConfig::default(), |_, _| Hosting::None).await;
    let mut owner = connect(server.address).await;
    say(&mut owner, hello(json!({"type": "create"}), None)).await;
    let welcome = until(&mut owner, of_type("welcome")).await;
    let mut watcher = connect(server.address).await;
    let access = json!({"type": "watch", "session": welcome["session"]});
    say(&mut watcher, hello(access, None)).await;
    until(&mut watcher, of_type("snapshot")).await;

    // Excess cursors are dropped without a word, and a fraction outside [0, 1] is clamped.
    let at = json!({"region": "main", "scope": "welcome", "x": 0.5, "y": 2.0});
    for _ in 0..200 {
        say(&mut owner, json!({"type": "cursor", "at": at})).await;
    }
    say(&mut owner, json!({"type": "setOptions", "explore": true})).await;
    let mut cursors = Vec::new();
    until(&mut watcher, |m| {
        if m["type"] == "cursor" {
            cursors.push(m["at"]["y"].clone());
        }
        m["options"]["explore"] == true
    })
    .await;
    assert!(
        !cursors.is_empty() && cursors.len() < 100,
        "{}",
        cursors.len()
    );
    assert!(cursors.iter().all(|y| *y == 1.0));

    // Other messages are limited to 10 a second.
    for _ in 0..30 {
        say(&mut owner, json!({"type": "setOptions", "explore": false})).await;
    }
    assert_eq!(closed(&mut owner).await, 4429);

    // A message above 16 KiB ends the socket.
    let filler = "a".repeat(20 * 1024);
    say(
        &mut watcher,
        json!({"type": "cursor", "at": null, "filler": filler}),
    )
    .await;
    assert!(matches!(closed(&mut watcher).await, 0 | 1009));
}

/// Sends a bare HTTP request and returns the status line.
async fn status(address: &str, path: &str) -> String {
    let mut stream = TcpStream::connect(address).await.unwrap();
    let request = format!("GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).await.unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).await.unwrap();
    response.lines().next().unwrap().to_owned()
}

#[tokio::test]
async fn the_desktop_opens_its_listener_for_the_first_session_and_closes_it_after_the_last() {
    let config = ShareConfig {
        listener_poll: Duration::from_millis(50),
        ..ShareConfig::default()
    };
    let server = start(Policy::Local, config, |state, _| {
        Hosting::Lan(ShareListener::new("127.0.0.1:0".parse().unwrap(), state))
    })
    .await;
    let mut owner = connect(server.address).await;
    say(&mut owner, hello(json!({"type": "create"}), None)).await;
    let welcome = until(&mut owner, of_type("welcome")).await;
    if !cfg!(feature = "embed-assets") {
        assert_eq!(
            welcome["link"],
            Value::Null,
            "a development build serves no frontend"
        );
        return;
    }
    let link = welcome["link"].as_str().unwrap();
    let (base, fragment) = link.split_once("/#join=").unwrap();
    let lan = base.strip_prefix("http://").unwrap();

    // Only the share socket and /api/info are served there, and only join links open it.
    assert!(status(lan, "/api/info").await.contains("200"));
    for path in ["/api/settings", "/api/share/sessions"] {
        assert!(status(lan, path).await.contains("404"), "{path}");
    }
    let mut guest = connect_async(format!("ws://{lan}/api/share/socket"))
        .await
        .unwrap()
        .0;
    say(
        &mut guest,
        hello(json!({"type": "join", "link": fragment}), None),
    )
    .await;
    until(&mut guest, of_type("welcome")).await;
    let mut intruder = connect_async(format!("ws://{lan}/api/share/socket"))
        .await
        .unwrap()
        .0;
    say(&mut intruder, hello(json!({"type": "create"}), Some("pw"))).await;
    assert_eq!(closed(&mut intruder).await, 4401);

    say(&mut owner, json!({"type": "leave"})).await;
    let mut shut = false;
    for _ in 0..40 {
        tokio::time::sleep(Duration::from_millis(50)).await;
        if TcpStream::connect(lan).await.is_err() {
            shut = true;
            break;
        }
    }
    assert!(shut, "the listener stayed open after the last session");
}
