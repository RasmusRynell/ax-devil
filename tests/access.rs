//! Who may reach what: the Local guard, the server password, the headers on every response, the socket limits, and
//! the `serve` command's start-up output.

use ax_devil::{
    AppState,
    access::{Admission, Limits, Policy, Refused},
    app, lan_app,
    plugins::Plugins,
    video_cache,
};
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode},
};
use handlers::Handlers;
use serde_json::{Value, json};
use sessions::Secret;
use std::{
    io::{BufRead, BufReader, Read},
    net::{IpAddr, Ipv4Addr},
    process::{Command, ExitStatus, Stdio},
    sync::{Arc, mpsc},
    time::{Duration, Instant},
};
use tokio::sync::watch;
use tower::ServiceExt;

/// What a state needs to stay alive: its folder, and the stop signal that would end its long-lived connections.
type Keep = (tempfile::TempDir, watch::Sender<bool>);

fn state(policy: Policy) -> (AppState, Keep) {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = video_cache::start(&settings, &cache);
    let (stop, stopping) = watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        policy,
        videos,
        stopping,
        Plugins::fixed(Handlers::built_in()),
        &cache,
    );
    (state, (folder, stop))
}

fn router(policy: Policy) -> (Router, Keep) {
    let (state, keep) = state(policy);
    (app(state), keep)
}

fn shared(password: &str) -> Policy {
    Policy::Shared {
        password: Some(Secret::chosen(password.into()).unwrap()),
    }
}

/// Sends a request and checks the one thing every response must carry, whatever its status.
async fn send(
    app: &Router,
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
    body: &str,
) -> (StatusCode, Value) {
    let mut request = Request::builder().method(method).uri(path);
    for (name, value) in headers {
        request = request.header(*name, *value);
    }
    let response = app
        .clone()
        .oneshot(
            request
                .header("content-type", "application/json")
                .body(Body::from(body.to_owned()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let headers: HeaderMap = response.headers().clone();
    assert_eq!(
        headers["content-security-policy"],
        "frame-ancestors 'none'; default-src 'self'; img-src 'self' data:",
        "{method} {path}"
    );
    assert_eq!(headers["x-frame-options"], "DENY", "{method} {path}");
    let bytes = to_bytes(response.into_body(), 1 << 20).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

#[tokio::test]
async fn the_local_listener_answers_only_pages_it_served_and_every_response_forbids_framing() {
    let (app, _folder) = router(Policy::Local);
    for host in ["127.0.0.1:7330", "localhost", "[::1]:1"] {
        let origin = format!("http://{host}");
        for headers in [
            vec![("host", host)],
            vec![("host", host), ("origin", &origin)],
        ] {
            let (status, info) = send(&app, "GET", "/api/info", &headers, "").await;
            assert_eq!(status, StatusCode::OK, "{headers:?}");
            assert_eq!(
                info["grants"],
                json!(["preferences", "configuration", "sessions", "files"])
            );
        }
    }
    let rebound = [("host", "evil.example")];
    let foreign = [("host", "127.0.0.1"), ("origin", "http://evil.example")];
    let opaque = [("host", "127.0.0.1"), ("origin", "null")];
    // The same host and port under another scheme is another origin.
    let secure = [
        ("host", "127.0.0.1:7330"),
        ("origin", "https://127.0.0.1:7330"),
    ];
    for headers in [
        &rebound[..],
        &foreign[..],
        &opaque[..],
        &secure[..],
        &[][..],
    ] {
        for (method, path) in [
            ("GET", "/api/info"),
            ("GET", "/api/settings"),
            ("PUT", "/api/settings"),
            ("GET", "/api/sessions"),
            ("GET", "/api/handlers"),
            ("GET", "/api/plugins"),
            ("POST", "/api/resolvers/mot_challenge/scan"),
            ("GET", "/api/session"),
            ("GET", "/api/playback"),
            ("GET", "/"),
        ] {
            let (status, _) = send(&app, method, path, headers, "{}").await;
            assert_eq!(status, StatusCode::FORBIDDEN, "{method} {path} {headers:?}");
        }
    }
    let (status, _) = send(&app, "GET", "/missing.js", &[("host", "127.0.0.1")], "").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn a_server_answers_settings_and_sessions_only_to_the_password() {
    let (app, _folder) = router(shared("secret"));
    let none: [(&str, &str); 0] = [];
    let wrong = [("authorization", "Bearer guess")];
    let right = [("authorization", "Bearer secret")];

    let (_, info) = send(&app, "GET", "/api/info", &none, "").await;
    assert_eq!(info["grants"], json!([]));
    assert_eq!(info["shell"], api_types::SHELL_API);
    let (_, info) = send(&app, "GET", "/api/info", &right, "").await;
    assert_eq!(
        info["grants"],
        json!(["configuration", "sessions", "files"])
    );

    for headers in [&none[..], &wrong[..]] {
        for (method, path) in [
            ("GET", "/api/settings"),
            ("PUT", "/api/settings"),
            ("GET", "/api/sessions"),
            ("GET", "/api/handlers"),
            ("GET", "/api/plugins"),
            ("POST", "/api/resolvers/mot_challenge/scan"),
        ] {
            let (status, _) = send(&app, method, path, headers, "{}").await;
            assert_eq!(status, StatusCode::UNAUTHORIZED, "{method} {path}");
        }
    }
    for path in ["/api/sessions", "/api/handlers", "/api/plugins"] {
        assert_eq!(send(&app, "GET", path, &right, "").await.0, StatusCode::OK);
    }
    // The password holder sees and replaces the Server settings, and may touch neither a Personal setting nor a
    // shortcut.
    let server = json!({
        "defaults.device.protocol": "https",
        "playback.video_cache": "auto",
        "storage.cache_dir": "",
        "storage.catalog_dir": "",
    });
    let (status, settings) = send(&app, "GET", "/api/settings", &right, "").await;
    assert_eq!(
        (status, settings),
        (
            StatusCode::OK,
            json!({"values": server, "shortcuts": {}, "restart": []})
        )
    );
    let change = |values: serde_json::Value, shortcuts: serde_json::Value| {
        json!({"values": values, "shortcuts": shortcuts}).to_string()
    };
    assert_eq!(
        send(
            &app,
            "PUT",
            "/api/settings",
            &right,
            &change(server.clone(), json!({}))
        )
        .await
        .0,
        StatusCode::OK
    );
    let mut with_theme = server.clone();
    with_theme["ui.theme"] = json!("dark");
    let (status, error) = send(
        &app,
        "PUT",
        "/api/settings",
        &right,
        &change(with_theme, json!({})),
    )
    .await;
    assert_eq!(
        (status, &error["location"]),
        (StatusCode::FORBIDDEN, &json!("ui.theme"))
    );
    let (status, error) = send(
        &app,
        "PUT",
        "/api/settings",
        &right,
        &change(server, json!({"exit": null})),
    )
    .await;
    assert_eq!(
        (status, &error["location"]),
        (StatusCode::FORBIDDEN, &json!("shortcuts.exit"))
    );
}

#[tokio::test]
async fn the_listener_for_other_machines_serves_the_sockets_but_neither_settings_nor_the_session_list_nor_the_files()
 {
    let (state, _keep) = state(Policy::Shared { password: None });
    let lan = lan_app(state);
    let host = [("host", "192.168.1.20:7340")];
    for path in [
        "/api/settings",
        "/api/sessions",
        "/api/handlers",
        "/api/plugins",
    ] {
        let (status, _) = send(&lan, "GET", path, &host, "").await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
    }
    // A plain request is no upgrade, but it reaches the sockets, which the links' holders use.
    for path in ["/api/session", "/api/playback"] {
        let (status, _) = send(&lan, "GET", path, &host, "").await;
        assert_ne!(status, StatusCode::NOT_FOUND, "{path}");
    }
}

#[test]
fn one_peer_or_one_crowd_cannot_use_up_the_sockets() {
    let admission = Arc::new(Admission::new(Limits::default()));
    let peer = |last: u8| IpAddr::V4(Ipv4Addr::new(10, 0, 0, last));

    // A peer may have 4 sockets waiting for their first message, and 16 open in all.
    let mut slots: Vec<_> = (0..4).map(|_| admission.admit(peer(1)).unwrap()).collect();
    assert_eq!(admission.admit(peer(1)).err(), Some(Refused::PeerBusy));
    for slot in &mut slots {
        slot.hello_received();
    }
    slots.extend((4..16).map(|_| {
        let mut slot = admission.admit(peer(1)).unwrap();
        slot.hello_received();
        slot
    }));
    assert_eq!(admission.admit(peer(1)).err(), Some(Refused::PeerBusy));
    drop(slots.pop());
    assert!(admission.admit(peer(1)).is_ok());

    // Sixteen peers with 4 waiting each fill the 64 server-wide, and the next peer is turned away too.
    let waiting: Vec<_> = (2..18)
        .flat_map(|last| (0..4).map(move |_| last))
        .map(|last| admission.admit(peer(last)).unwrap())
        .collect();
    assert_eq!(admission.admit(peer(100)).err(), Some(Refused::Busy));
    drop(waiting);
    assert!(admission.admit(peer(100)).is_ok());
}

#[test]
fn behind_a_proxy_only_the_totals_bind() {
    let proxy = IpAddr::V4(Ipv4Addr::new(10, 0, 0, 1));
    let fill = |admission: &Arc<Admission>, count: usize| {
        (0..count)
            .map(|_| {
                let mut slot = admission.admit(proxy)?;
                slot.hello_received();
                Ok(slot)
            })
            .collect::<Result<Vec<_>, Refused>>()
    };
    let direct = Arc::new(Admission::new(Limits::default()));
    assert_eq!(fill(&direct, 17).err(), Some(Refused::PeerBusy));

    let proxied = Arc::new(Admission::new(Limits::behind_proxy()));
    let open = fill(&proxied, 256).unwrap();
    assert_eq!(proxied.admit(proxy).err(), Some(Refused::Busy));
    drop(open);
}

/// Starts `serve --port 0` with its own home and returns what it prints to stdout and stderr, and how it ended: its
/// exit status, or `None` when it was still running once it had announced its URL and was killed.
fn serve_output(extra: &[&str]) -> (Vec<String>, String, Option<ExitStatus>) {
    let home = tempfile::tempdir().unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .args(["serve", "--port", "0"])
        .args(extra)
        .env("HOME", home.path())
        .env_remove("AX_DEVIL_SERVER_PASSWORD")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let (lines, received) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if lines.send(line).is_err() {
                break;
            }
        }
    });
    let mut printed = Vec::new();
    // The URL line ends the announcement; a failed start closes stdout instead.
    while let Ok(line) = received.recv_timeout(Duration::from_secs(20)) {
        let done = line.starts_with("ax-devil: ");
        printed.push(line);
        if done {
            break;
        }
    }
    // Closed stdout comes before the exit is reapable, so a start that failed is waited for, not sampled.
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut exit = child.try_wait().unwrap();
    while exit.is_none()
        && printed
            .last()
            .is_none_or(|line| !line.starts_with("ax-devil: "))
    {
        if Instant::now() > deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
        exit = child.try_wait().unwrap();
    }
    if exit.is_none() {
        child.kill().unwrap();
        child.wait().unwrap();
    }
    let mut log = String::new();
    child
        .stderr
        .take()
        .unwrap()
        .read_to_string(&mut log)
        .unwrap();
    (printed, log, exit)
}

#[test]
fn serve_prints_a_generated_password_and_warns_about_plain_http_off_loopback() {
    if !cfg!(feature = "embed-assets") {
        let (printed, log, exit) = serve_output(&[]);
        assert!(exit.is_some_and(|status| !status.success()), "{log}");
        assert!(printed.is_empty(), "{printed:?}");
        assert!(log.contains("make build"), "{log}");
        return;
    }
    let (printed, log, exit) = serve_output(&["--bind", "0.0.0.0"]);
    assert!(exit.is_none(), "{log}");
    let password = printed[0]
        .strip_prefix("ax-devil server password: ")
        .unwrap();
    assert_eq!(password.len(), 32);
    assert!(password.bytes().all(|byte| byte.is_ascii_hexdigit()));
    let url = printed[1].strip_prefix("ax-devil: http://").unwrap();
    assert!(url.ends_with(&format!("/#password={password}")), "{url}");
    assert_eq!(log.matches("WARN").count(), 1, "{log}");
    assert!(log.contains("plain HTTP"), "{log}");

    let (_, log, _) = serve_output(&[]);
    assert!(!log.contains("WARN"), "loopback needs no warning: {log}");
}
