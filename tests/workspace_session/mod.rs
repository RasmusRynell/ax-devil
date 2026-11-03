//! What the saved-workspace tests share: a Local backend over temporary recovery and recents folders, and the Session
//! socket messages they send and read.

// Each test crate that includes this module uses a different part of it.
#![allow(dead_code)]

use crate::session_client::Socket;
use ax_devil::{
    AppState,
    access::Policy,
    app,
    session::{Launch, LaunchQueue, WorkspaceStorage},
};
use serde_json::{Value, json};
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use workspace_files::{Recents, RecoveryFolder, SUFFIX};

/// A Local backend whose recovery copies and recents are in `folder`, over fresh sessions. `launch` is what its first
/// window starts from. Returns its address.
pub async fn backend(folder: &Path, launch: Option<Launch>) -> String {
    let settings = settings::Settings::load(&folder.join("default.json")).unwrap();
    let cache = folder.join("cache");
    let videos = ax_devil::video_cache::start(&settings, &cache);
    // Kept by the server task, so the backend never sees its stop signal.
    let (stop, stopping) = tokio::sync::watch::channel(false);
    let state = AppState::new(
        Arc::new(settings),
        Policy::Local,
        videos,
        stopping,
        handlers::Handlers::built_in(),
        &cache,
    )
    .with_workspace_storage(WorkspaceStorage {
        recents: recents(folder),
        recovery: RecoveryFolder::new(folder.join("unsaved")),
    })
    .with_launch(Arc::new(LaunchQueue::new(launch)));
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let service = app(state).into_make_service_with_connect_info::<SocketAddr>();
        axum::serve(listener, service).await.unwrap();
        drop(stop);
    });
    address.to_string()
}

pub fn recents(folder: &Path) -> Recents {
    Recents::new(folder.join("recent.json"))
}

/// The recovery copies on disk.
pub fn copies(folder: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(folder.join("unsaved")) else {
        return Vec::new();
    };
    let mut copies: Vec<_> = entries
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.to_string_lossy().ends_with(SUFFIX))
        .collect();
    copies.sort();
    copies
}

pub fn newest_recent(folder: &Path) -> Option<PathBuf> {
    recents(folder)
        .list()
        .into_iter()
        .next()
        .map(|recent| recent.path)
}

/// How many items the recovery copy `copy` holds, once it is a whole document: reserving it creates an empty file
/// first.
pub fn items_in(copy: &Path) -> Option<usize> {
    let document: Value = serde_json::from_slice(&std::fs::read(copy).ok()?).ok()?;
    document["items"].as_array().map(Vec::len)
}

/// Waits, bounded, for something another task does on its own schedule.
pub async fn eventually(what: &str, mut done: impl FnMut() -> bool) {
    for _ in 0..200 {
        if done() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("{what} did not happen");
}

pub fn add_video(seq: u64) -> Value {
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4");
    let path = video.display().to_string();
    json!({"type": "command", "seq": seq, "cmd": {"type": "content", "command": {
        "type": "addItems", "open": false, "items": [{"type": "Video", "path": path}]}}})
}

pub fn workspace(seq: u64, command: Value) -> Value {
    json!({"type": "command", "seq": seq, "cmd": {"type": "workspace", "command": command}})
}

/// The next Doc whose workspace satisfies `wanted`.
pub async fn doc(socket: &mut Socket, wanted: impl Fn(&Value) -> bool) -> Value {
    let message = socket
        .until(|message| message["type"] == "doc" && wanted(&message["doc"]["workspace"]))
        .await;
    message["doc"]["workspace"].clone()
}

/// Reads until the Ack of `seq` and a Doc whose workspace satisfies `wanted` have both arrived, in either order.
pub async fn settle(
    socket: &mut Socket,
    seq: u64,
    wanted: impl Fn(&Value) -> bool,
) -> (Value, Value) {
    let (mut outcome, mut workspace) = (None, None);
    socket
        .until(|message| {
            if message["type"] == "ack" && message["seq"] == seq {
                outcome = Some(message["outcome"].clone());
            } else if message["type"] == "doc" && wanted(&message["doc"]["workspace"]) {
                workspace = Some(message["doc"]["workspace"].clone());
            }
            outcome.is_some() && workspace.is_some()
        })
        .await;
    (outcome.unwrap(), workspace.unwrap())
}

pub async fn ack(socket: &mut Socket, seq: u64) -> Value {
    socket
        .until(|message| message["type"] == "ack" && message["seq"] == seq)
        .await["outcome"]
        .clone()
}
