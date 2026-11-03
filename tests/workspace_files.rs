//! Saved workspaces over the Session socket: unsaved work survives the window and the backend, a second window cannot
//! take a copy another one holds, and a file that cannot be opened changes nothing.

mod session_client;

use ax_devil::{
    AppState,
    access::Policy,
    app,
    session::{Launch, LaunchQueue, WorkspaceStorage},
};
use serde_json::{Value, json};
use session_client::Socket;
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use workspace_files::{Recents, RecoveryFolder, SUFFIX};

/// A Local backend whose recovery copies and recents are in `folder`, over fresh sessions. `launch` is what its first
/// window starts from. Returns its address.
async fn backend(folder: &Path, launch: Option<Launch>) -> String {
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

fn recents(folder: &Path) -> Recents {
    Recents::new(folder.join("recent.json"))
}

/// The recovery copies on disk.
fn copies(folder: &Path) -> Vec<PathBuf> {
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

fn newest_recent(folder: &Path) -> Option<PathBuf> {
    recents(folder)
        .list()
        .into_iter()
        .next()
        .map(|recent| recent.path)
}

/// How many items the recovery copy `copy` holds, once it is a whole document: reserving it creates an empty file
/// first.
fn items_in(copy: &Path) -> Option<usize> {
    let document: Value = serde_json::from_slice(&std::fs::read(copy).ok()?).ok()?;
    document["items"].as_array().map(Vec::len)
}

/// Waits, bounded, for something another task does on its own schedule.
async fn eventually(what: &str, mut done: impl FnMut() -> bool) {
    for _ in 0..200 {
        if done() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("{what} did not happen");
}

fn add_video(seq: u64) -> Value {
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4");
    let path = video.display().to_string();
    json!({"type": "command", "seq": seq, "cmd": {"type": "content", "command": {
        "type": "addItems", "open": false, "items": [{"type": "Video", "path": path}]}}})
}

fn workspace(seq: u64, command: Value) -> Value {
    json!({"type": "command", "seq": seq, "cmd": {"type": "workspace", "command": command}})
}

/// The next Doc whose workspace satisfies `wanted`.
async fn doc(socket: &mut Socket, wanted: impl Fn(&Value) -> bool) -> Value {
    let message = socket
        .until(|message| message["type"] == "doc" && wanted(&message["doc"]["workspace"]))
        .await;
    message["doc"]["workspace"].clone()
}

/// Reads until the Ack of `seq` and a Doc whose workspace satisfies `wanted` have both arrived, in either order.
async fn settle(socket: &mut Socket, seq: u64, wanted: impl Fn(&Value) -> bool) -> (Value, Value) {
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

async fn ack(socket: &mut Socket, seq: u64) -> Value {
    socket
        .until(|message| message["type"] == "ack" && message["seq"] == seq)
        .await["outcome"]
        .clone()
}

#[tokio::test]
async fn unsaved_work_survives_the_window_and_one_window_holds_a_copy_at_a_time() {
    let folder = tempfile::tempdir().unwrap();
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let address = backend(folder.path(), None).await;

    // An edit is written to a recovery copy, which is the newest recent workspace.
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    eventually("the recovery copy, listed", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(1) && newest_recent(folder.path()) == copy
    })
    .await;

    // Save As writes the file and ends the copy.
    let save = workspace(
        2,
        json!({"type": "save", "path": saved.display().to_string()}),
    );
    owner.say(save).await;
    assert_eq!(ack(&mut owner, 2).await, json!({"status": "ok"}));
    assert!(saved.is_file());
    assert!(copies(folder.path()).is_empty());
    assert_eq!(newest_recent(folder.path()), Some(saved.clone()));

    // Leaving with an unsaved edit keeps a copy of it.
    owner.say(add_video(3)).await;
    owner.say(json!({"type": "leave"})).await;
    // Whole, listed, and let go of by the ended session, so the next backend can take it.
    let unsaved = RecoveryFolder::new(folder.path().join("unsaved"));
    eventually("the copy of the second edit", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(2)
            && newest_recent(folder.path()) == copy
            && copy.is_some_and(|copy| unsaved.claim(&copy).is_ok())
    })
    .await;

    // The next backend starts from that copy, still modified and offered Keep or Discard.
    let copy = copies(folder.path()).remove(0);
    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut window = Socket::own(&address, None).await;
    let restored = doc(&mut window, |ws| ws["recovered"] == true).await;
    assert_eq!(restored["items"].as_array().unwrap().len(), 2);
    assert_eq!(restored["modified"], true);
    assert_eq!(restored["file"], json!(saved.display().to_string()));

    // A second window cannot take the copy the first one holds.
    let mut second = Socket::own(&address, None).await;
    let open = workspace(
        1,
        json!({"type": "open", "path": copy.display().to_string()}),
    );
    // The window that holds the copy may open it from the recents: nothing changes, and it stays held.
    let own = workspace(
        5,
        json!({"type": "open", "path": copy.display().to_string()}),
    );
    window.say(own).await;
    assert_eq!(ack(&mut window, 5).await, json!({"status": "ok"}));
    second.say(open).await;
    let outcome = ack(&mut second, 1).await;
    assert_eq!(outcome["status"], "failed");
    assert_eq!(outcome["error"]["code"], "WorkspaceInUse");

    // `ax-devil open` of a copy another window holds starts empty, and no window writes over the copy.
    let held = Launch::Open {
        path: copy.clone(),
        workspace: None,
    };
    let mut cli = Socket::own(&backend(folder.path(), Some(held)).await, None).await;
    let started = doc(&mut cli, |_| true).await;
    assert_eq!(
        (&started["items"], &started["recovered"]),
        (&json!([]), &json!(false))
    );
    let over = |path: &Path| {
        workspace(
            2,
            json!({"type": "save", "path": path.display().to_string()}),
        )
    };
    window.say(over(&copy)).await;
    assert_eq!(
        ack(&mut window, 2).await,
        json!({"status": "refused", "code": "Invalid"})
    );
    #[cfg(unix)]
    {
        let alias = folder.path().join("alias");
        std::os::unix::fs::symlink(folder.path().join("unsaved"), &alias).unwrap();
        window
            .say(over(&alias.join(copy.file_name().unwrap())))
            .await;
        assert_eq!(ack(&mut window, 2).await["status"], "refused");
    }
    assert_eq!(items_in(&copy), Some(2));

    // Discard goes back to the saved file and deletes the copy, before it is acknowledged.
    window.say(workspace(1, json!({"type": "discard"}))).await;
    let (outcome, reopened) = settle(&mut window, 1, |ws| ws["recovered"] == false).await;
    assert_eq!(outcome, json!({"status": "ok"}));
    assert_eq!(reopened["items"].as_array().unwrap().len(), 1);
    assert_eq!(reopened["modified"], false);
    assert!(copies(folder.path()).is_empty());

    // A file that is not a workspace changes nothing and leaves the recents.
    let broken = folder.path().join(format!("broken{SUFFIX}"));
    std::fs::write(&broken, "{not json").unwrap();
    recents(folder.path()).record(&broken, 1).unwrap();
    window
        .say(workspace(
            2,
            json!({"type": "open", "path": broken.display().to_string()}),
        ))
        .await;
    assert_eq!(ack(&mut window, 2).await["status"], "failed");
    assert!(
        recents(folder.path())
            .list()
            .iter()
            .all(|recent| recent.path != broken)
    );
    window.say(add_video(3)).await;
    let unchanged = doc(&mut window, |ws| ws["items"].as_array().unwrap().len() == 2).await;
    assert_eq!(unchanged["epoch"], reopened["epoch"]);
}
