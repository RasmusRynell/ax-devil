//! `ax-devil local --video PATH` through the real binary: it opens an Untitled workspace holding that one video.

use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::Path,
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

/// A running backend, stopped when dropped.
struct Backend {
    child: Child,
    address: String,
}

impl Backend {
    fn start(folder: &Path, video: &Path) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("--config")
            .arg(folder.join("default.json"))
            .arg("--cache-dir")
            .arg(folder.join("cache"))
            .arg("local")
            .arg("--video")
            .arg(video)
            .env("HOME", folder)
            .env("PORT", "0")
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let ready = BufReader::new(stdout)
            .lines()
            .map_while(Result::ok)
            .find_map(|line| line.strip_prefix("ax-devil: http://").map(str::to_owned));
        let address = ready.expect("the backend exited before it was ready");
        Self { child, address }
    }

    /// The workspace once nothing in it is pending.
    fn settled_workspace(&self) -> Value {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let workspace = self.get("/api/workspace");
            let pending = workspace["items"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item["resolution"]["status"] == "Pending");
            if !pending || Instant::now() > deadline {
                return workspace;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn get(&self, path: &str) -> Value {
        let mut stream = TcpStream::connect(&self.address).unwrap();
        write!(
            stream,
            "GET {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
            self.address
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        let (_, body) = response.split_once("\r\n\r\n").unwrap();
        serde_json::from_str(body).unwrap()
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[test]
fn local_video_opens_an_untitled_workspace_with_one_resolved_item() {
    let folder = tempfile::tempdir().unwrap();
    let video = folder.path().join("gate.mp4");
    std::fs::write(&video, "x").unwrap();

    let workspace = Backend::start(folder.path(), &video).settled_workspace();

    assert_eq!(workspace["name"], Value::Null);
    let items = workspace["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["label"], "gate.mp4");
    assert_eq!(items[0]["open"], true);
    assert_eq!(items[0]["resolution"]["status"], "Resolved");
}
