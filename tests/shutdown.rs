//! Ctrl+C with a viewer on a playing video: the backend ends its sessions, stops its decoders and exits by itself,
//! well within the time its own deadlines allow.

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};
use tokio_tungstenite::tungstenite::Message;

fn get(address: &str, path: &str) -> Value {
    let mut stream = TcpStream::connect(address).unwrap();
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n"
    )
    .unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
}

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
    let deadline = Instant::now() + Duration::from_secs(10);
    let content_id = loop {
        let workspace = get(&address, "/api/workspace");
        if let Some(id) = workspace["items"][0]["resolution"]["content"]["id"].as_str() {
            break id.to_owned();
        }
        assert!(Instant::now() < deadline, "the video never resolved");
        std::thread::sleep(Duration::from_millis(20));
    };

    let (mut viewer, _) = tokio_tungstenite::connect_async(format!("ws://{address}/api/playback"))
        .await
        .unwrap();
    viewer
        .send(Message::text(
            json!({"type": "Open", "content_id": content_id}).to_string(),
        ))
        .await
        .unwrap();
    // The first frame is on its way: the source is open and its decoders are running.
    loop {
        match tokio::time::timeout(Duration::from_secs(10), viewer.next())
            .await
            .expect("no frame arrived")
        {
            Some(Ok(Message::Binary(bytes))) if bytes[5] == 1 => break,
            Some(Ok(_)) => {}
            other => panic!("the viewer was cut off: {other:?}"),
        }
    }

    // Stopping ends the viewer's session: its socket is closed from the backend's side, not left open.
    let sent = Command::new("kill")
        .args(["-INT", &backend.id().to_string()])
        .status()
        .unwrap();
    assert!(sent.success());
    loop {
        match tokio::time::timeout(Duration::from_secs(10), viewer.next())
            .await
            .expect("the viewer's socket stayed open")
        {
            Some(Ok(Message::Close(_)) | Err(_)) | None => break,
            Some(Ok(_)) => {}
        }
    }
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
