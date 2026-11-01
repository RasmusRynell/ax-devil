//! `ax-devil local --video PATH` and `ax-devil playlist ...` through the real binary: the first window's session holds
//! that one item, with its viewer requested, in an Untitled workspace.

mod session_client;

use serde_json::Value;
use session_client::Socket;
use std::{
    ffi::OsStr,
    io::{BufRead, BufReader},
    path::Path,
    process::{Child, Command, Stdio},
};

/// A running backend, stopped when dropped.
struct Backend {
    child: Child,
    address: String,
}

impl Backend {
    /// Starts `ax-devil` with the private files in `folder` and `command` after its options.
    fn start(folder: &Path, command: &[&OsStr]) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("--config")
            .arg(folder.join("default.json"))
            .arg("--cache-dir")
            .arg(folder.join("cache"))
            .args(command)
            .env("HOME", folder)
            .env("XDG_CONFIG_HOME", folder.join("config"))
            .env("XDG_STATE_HOME", folder.join("state"))
            .env("XDG_CACHE_HOME", folder.join("cache-home"))
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
}

impl Drop for Backend {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[tokio::test]
async fn local_video_opens_an_untitled_workspace_with_one_resolved_item_for_the_first_window_only()
{
    let folder = tempfile::tempdir().unwrap();
    let video = folder.path().join("gate.mp4");
    std::fs::write(&video, "x").unwrap();
    let backend = Backend::start(
        folder.path(),
        &["local".as_ref(), "--video".as_ref(), video.as_ref()],
    );

    let mut first = Socket::own(&backend.address, None).await;
    let workspace = first.settled_doc().await["doc"]["workspace"].clone();

    assert_eq!(workspace["name"], Value::Null);
    let items = workspace["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["label"], "gate.mp4");
    assert_eq!(items[0]["open"], true);
    assert_eq!(items[0]["resolution"]["status"], "Resolved");

    // The video belongs to the first window; a second window starts empty.
    let mut second = Socket::own(&backend.address, None).await;
    let doc = second.until(|message| message["type"] == "doc").await;
    assert_eq!(doc["doc"]["workspace"]["items"], serde_json::json!([]));
}

#[tokio::test]
async fn a_playlist_command_line_opens_the_dataset_as_one_item_with_its_entries() {
    let folder = tempfile::tempdir().unwrap();
    let dataset = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/datasets/mot");
    let backend = Backend::start(
        folder.path(),
        &[
            "playlist".as_ref(),
            "mot_challenge".as_ref(),
            dataset.as_ref(),
        ],
    );

    let mut first = Socket::own(&backend.address, None).await;
    let workspace = first.settled_doc().await["doc"]["workspace"].clone();

    let items = workspace["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["open"], true);
    let content = &items[0]["resolution"]["content"];
    assert_eq!(content["kind"], "Playlist");
    let mut entries: Vec<_> = content["entries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| entry["label"].as_str().unwrap())
        .collect();
    entries.sort_unstable();
    assert_eq!(entries, ["MINI-01", "MINI-02"]);
    assert!(workspace["rows"].as_array().unwrap().len() > 3);
}
