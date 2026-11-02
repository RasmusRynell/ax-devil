//! Ctrl+C with a viewer on a playing video: the backend ends its sessions, stops its decoders and exits by itself,
//! well within the time its own deadlines allow.

mod session_client;

use serde_json::json;
use session_client::Socket;
use std::{
    io::{BufRead, BufReader},
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[tokio::test]
async fn ctrl_c_ends_open_viewers_and_the_backend_exits() {
    let folder = tempfile::tempdir().unwrap();
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/long_gop_h264.mp4");
    let mut backend = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .arg("--config")
        .arg(folder.path().join("default.json"))
        .arg("--cache-dir")
        .arg(folder.path().join("cache"))
        .args(["local", "--video"])
        .arg(&video)
        .env("HOME", folder.path())
        .env("XDG_CONFIG_HOME", folder.path().join("config"))
        .env("XDG_STATE_HOME", folder.path().join("state"))
        .env("XDG_CACHE_HOME", folder.path().join("cache-home"))
        .env("PORT", "0")
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let stdout = backend.stdout.take().unwrap();
    let address = BufReader::new(stdout)
        .lines()
        .map_while(Result::ok)
        .find_map(|line| line.strip_prefix("ax-devil: http://").map(str::to_owned))
        .expect("the backend exited before it was ready");
    let mut session = Socket::own(&address, None).await;
    let doc = session.settled_doc().await;
    let content = &doc["doc"]["workspace"]["items"][0]["resolution"]["content"];
    let key = json!({
        "content": content["id"],
        "generation": content["generation"],
        "entry": content["entry"]["id"],
    });
    session
        .say(json!({"type": "command", "seq": 1, "cmd": {"type": "requestTicket", "key": key}}))
        .await;
    let ticket = session.until(|message| message["type"] == "ticket").await["ticket"].clone();

    let mut viewer = Socket::connect(&address, "/api/playback").await;
    viewer
        .say(json!({"type": "Open", "ticket": ticket, "data_bytes": 1 << 20}))
        .await;
    // The first frame is on its way: the source is open and its decoders are running.
    viewer.until_binary(|bytes| bytes[5] == 1).await;

    // Stopping ends the viewer's session: its socket is closed from the backend's side, not left open.
    let sent = Command::new("kill")
        .args(["-INT", &backend.id().to_string()])
        .status()
        .unwrap();
    assert!(sent.success());
    viewer.closed().await;
    let exited = tokio::task::spawn_blocking(move || {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = backend.try_wait().unwrap() {
                return Some(status);
            }
            if Instant::now() > deadline {
                let _ = backend.kill();
                let _ = backend.wait();
                return None;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    })
    .await
    .unwrap();
    assert!(
        exited.is_some_and(|status| status.success()),
        "the backend must exit by itself, successfully"
    );
}

#[test]
fn closing_stdin_stops_the_backend_only_when_asked_to() {
    let folder = tempfile::tempdir().unwrap();
    let start = |ask: bool| {
        let mut command = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
        command
            .arg("--config")
            .arg(folder.path().join("default.json"))
            .env("HOME", folder.path())
            .env("XDG_STATE_HOME", folder.path().join("state"))
            .env("XDG_CACHE_HOME", folder.path().join("cache-home"))
            .env("PORT", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped());
        if ask {
            command.env("AX_DEVIL_EXIT_ON_STDIN_CLOSE", "1");
        }
        let mut backend = command.spawn().unwrap();
        let stdout = backend.stdout.take().unwrap();
        let ready = BufReader::new(stdout)
            .lines()
            .map_while(Result::ok)
            .any(|line| line.starts_with("ax-devil: http://"));
        assert!(ready, "the backend exited before it was ready");
        backend
    };
    let wait = |backend: &mut std::process::Child, seconds: u64| {
        let deadline = Instant::now() + Duration::from_secs(seconds);
        while Instant::now() < deadline {
            if let Some(status) = backend.try_wait().unwrap() {
                return Some(status);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        None
    };

    let mut asked = start(true);
    drop(asked.stdin.take());
    assert!(wait(&mut asked, 10).is_some_and(|status| status.success()));

    let mut plain = start(false);
    drop(plain.stdin.take());
    assert!(
        wait(&mut plain, 1).is_none(),
        "a closed stdin must not stop a backend that was not asked to"
    );
    plain.kill().unwrap();
    let _ = plain.wait();
}
