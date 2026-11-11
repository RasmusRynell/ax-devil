//! Saved workspaces over the Session socket: unsaved work survives the window and the backend, a second window cannot
//! take a copy another one holds, and a file that cannot be opened changes nothing.

use crate::session_client;
use crate::workspace_session;

use ax_devil::session::{HandOff, Launch};
use serde_json::json;
use session_client::Socket;
use std::path::Path;
use workspace_files::{RecoveryFolder, SUFFIX};
use workspace_session::*;

#[tokio::test]
async fn unsaved_work_survives_the_window_and_one_window_holds_a_copy_at_a_time() {
    let folder = tempfile::tempdir().unwrap();
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let address = backend(folder.path(), None).await;

    // An edit is written to a recovery copy, which is the newest recent workspace.
    let mut owner = Socket::own(&address, None).await;
    let told = |m: &serde_json::Value| m["type"] == "changed" && m["topic"] == "recents";
    let before = owner.until(told).await["revision"].clone();
    owner.say(add_video(1)).await;
    // The Owner is told the list changed.
    owner.until(|m| told(m) && m["revision"] != before).await;
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
    // Through a symlinked folder it is the same copy: held by the window that has it, taken for the other.
    #[cfg(unix)]
    {
        let alias = folder.path().join("alias");
        std::os::unix::fs::symlink(folder.path().join("unsaved"), &alias).unwrap();
        let through = |seq| {
            let path = alias.join(copy.file_name().unwrap());
            workspace(
                seq,
                json!({"type": "open", "path": path.display().to_string()}),
            )
        };
        window.say(through(6)).await;
        assert_eq!(ack(&mut window, 6).await, json!({"status": "ok"}));
        second.say(through(2)).await;
        assert_eq!(ack(&mut second, 2).await["error"]["code"], "WorkspaceInUse");
    }

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

#[tokio::test]
async fn recovery_failure_does_not_prevent_replacement() {
    for blocked in ["unsaved", "recent.json"] {
        let folder = tempfile::tempdir().unwrap();
        let obstruction = folder.path().join(blocked);
        if blocked == "unsaved" {
            std::fs::write(&obstruction, "not a directory").unwrap();
        } else {
            std::fs::create_dir(&obstruction).unwrap();
        }
        let target = folder.path().join(format!("other{SUFFIX}"));
        workspace_files::save(&workspace::Workspace::default(), &target).unwrap();
        let address = backend(folder.path(), None).await;
        let mut owner = Socket::own(&address, None).await;
        for (seq, command) in [
            (2, json!({"type": "new"})),
            (4, json!({"type": "open", "path": target})),
        ] {
            owner.say(add_video(seq - 1)).await;
            doc(&mut owner, |ws| ws["modified"] == true).await;
            owner.say(workspace(seq, command)).await;
            let (outcome, empty) = settle(&mut owner, seq, |ws| ws["items"] == json!([])).await;
            assert_eq!(outcome, json!({"status": "ok"}), "{blocked}");
            assert_eq!(empty["modified"], false);
        }
    }
}

#[tokio::test]
async fn save_as_replaces_another_file_only_when_allowed() {
    let folder = tempfile::tempdir().unwrap();
    let other = folder.path().join(format!("other{SUFFIX}"));
    std::fs::write(&other, "someone else's").unwrap();
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    let rename = json!({"type": "command", "seq": 5, "cmd": {"type": "content", "command": {
        "type": "renameWorkspace", "name": "  Review  ", "epoch": 0}}});
    owner.say(rename).await;
    assert_eq!(ack(&mut owner, 5).await, json!({"status": "ok"}));
    let save = |seq, replace| {
        let path = other.display().to_string();
        workspace(
            seq,
            json!({"type": "save", "path": path, "replace": replace}),
        )
    };

    owner.say(save(2, false)).await;
    let outcome = ack(&mut owner, 2).await;
    assert_eq!(outcome["error"]["code"], "WorkspaceExists");
    assert_eq!(std::fs::read_to_string(&other).unwrap(), "someone else's");

    owner.say(save(3, true)).await;
    assert_eq!(ack(&mut owner, 3).await, json!({"status": "ok"}));
    assert_eq!(items_in(&other), Some(1));
    let saved: serde_json::Value = serde_json::from_slice(&std::fs::read(&other).unwrap()).unwrap();
    assert_eq!(saved["name"], "Review");
    // It is the workspace's own file now, which never asks.
    owner.say(save(4, false)).await;
    assert_eq!(ack(&mut owner, 4).await, json!({"status": "ok"}));
}

#[tokio::test]
async fn a_launch_handed_to_the_running_backend_starts_the_next_window() {
    let folder = tempfile::tempdir().unwrap();
    let video = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4");
    let item = workspace::intake::video(&video, &[], &handlers::Handlers::built_in()).unwrap();
    let handed = HandOff::of(&Launch::Items(vec![item])).unwrap();
    let address = backend(folder.path(), Some(Launch::Restore)).await;

    session_client::post(
        &address,
        "/api/launch",
        &serde_json::to_value(handed).unwrap(),
    )
    .await;
    let mut window = Socket::own(&address, None).await;
    let started = doc(&mut window, |ws| {
        ws["items"].as_array().is_some_and(|i| i.len() == 1)
    })
    .await;
    assert_eq!(started["modified"], true);

    // A workspace file is opened by the window that takes it.
    let saved = folder.path().join(format!("handed{SUFFIX}"));
    let save = json!({"type": "save", "path": saved.display().to_string()});
    window.say(workspace(1, save)).await;
    assert_eq!(ack(&mut window, 1).await, json!({"status": "ok"}));
    let open = HandOff::of(&Launch::Open {
        path: saved,
        workspace: None,
    })
    .unwrap();
    session_client::post(
        &address,
        "/api/launch",
        &serde_json::to_value(open).unwrap(),
    )
    .await;
    let mut next = Socket::own(&address, None).await;
    let opened = doc(&mut next, |ws| {
        ws["items"].as_array().is_some_and(|i| i.len() == 1)
    })
    .await;
    assert_eq!(opened["modified"], false);
}
