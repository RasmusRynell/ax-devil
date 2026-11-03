//! Saved workspaces over the Session socket: unsaved work survives the window and the backend, a second window cannot
//! take a copy another one holds, and a file that cannot be opened changes nothing.

mod session_client;
mod workspace_session;

use ax_devil::session::Launch;
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

#[cfg(unix)]
#[tokio::test]
async fn work_that_cannot_be_kept_is_not_replaced() {
    use std::os::unix::fs::PermissionsExt;
    let folder = tempfile::tempdir().unwrap();
    let unsaved = folder.path().join("unsaved");
    std::fs::create_dir(&unsaved).unwrap();
    let mode =
        |mode| std::fs::set_permissions(&unsaved, std::fs::Permissions::from_mode(mode)).unwrap();
    mode(0o500);
    if std::fs::File::create(unsaved.join("probe")).is_ok() {
        mode(0o700);
        return; // running as a user the folder mode does not stop (root)
    }
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    doc(&mut owner, |ws| ws["modified"] == true).await;

    // New would drop the edit with nowhere to keep it, so it is refused and the workspace stays as it was.
    owner.say(workspace(2, json!({"type": "new"}))).await;
    let outcome = ack(&mut owner, 2).await;
    assert_eq!(outcome["status"], "failed");
    assert_eq!(outcome["error"]["code"], "SaveFailed");
    owner.say(add_video(3)).await;
    doc(&mut owner, |ws| ws["items"].as_array().unwrap().len() == 2).await;

    mode(0o700);
    owner.say(workspace(4, json!({"type": "new"}))).await;
    let (outcome, empty) = settle(&mut owner, 4, |ws| ws["items"] == json!([])).await;
    assert_eq!(outcome, json!({"status": "ok"}));
    assert_eq!(empty["modified"], false);
}

#[tokio::test]
async fn save_as_replaces_another_file_only_when_allowed() {
    let folder = tempfile::tempdir().unwrap();
    let other = folder.path().join(format!("other{SUFFIX}"));
    std::fs::write(&other, "someone else's").unwrap();
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
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
    // It is the workspace's own file now, which never asks.
    owner.say(save(4, false)).await;
    assert_eq!(ack(&mut owner, 4).await, json!({"status": "ok"}));
}
