//! Recovery copies in awkward spots: a saved file that is gone, a copy that is left behind, cannot be deleted, or is
//! pushed out of the recents by other windows.

mod session_client;
mod workspace_session;

use ax_devil::session::Launch;
use serde_json::json;
use session_client::Socket;
use workspace_files::{RecoveryFolder, SUFFIX};
use workspace_session::*;

#[tokio::test]
async fn discarding_changes_whose_saved_file_is_gone_starts_empty() {
    let folder = tempfile::tempdir().unwrap();
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    let save = json!({"type": "save", "path": saved.display().to_string()});
    owner.say(workspace(2, save)).await;
    assert_eq!(ack(&mut owner, 2).await, json!({"status": "ok"}));
    owner.say(add_video(3)).await;
    owner.say(json!({"type": "leave"})).await;
    let unsaved = RecoveryFolder::new(folder.path().join("unsaved"));
    eventually("the copy of the edit", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(2)
            && newest_recent(folder.path()) == copy
            && copy.is_some_and(|copy| unsaved.claim(&copy).is_ok())
    })
    .await;
    std::fs::remove_file(&saved).unwrap();

    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut window = Socket::own(&address, None).await;
    doc(&mut window, |ws| ws["recovered"] == true).await;
    window.say(workspace(1, json!({"type": "discard"}))).await;
    let (outcome, empty) = settle(&mut window, 1, |ws| ws["recovered"] == false).await;
    assert_eq!(outcome, json!({"status": "ok"}));
    assert_eq!(empty["items"], json!([]));
    assert_eq!(empty["modified"], false);
    assert!(copies(folder.path()).is_empty());
}

#[tokio::test]
async fn opening_a_file_after_its_unsaved_changes_means_the_next_launch_does_not_restore_them() {
    let folder = tempfile::tempdir().unwrap();
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    let save = json!({"type": "save", "path": saved.display().to_string()});
    owner.say(workspace(2, save)).await;
    assert_eq!(ack(&mut owner, 2).await, json!({"status": "ok"}));
    owner.say(add_video(3)).await;
    owner.say(json!({"type": "leave"})).await;
    let unsaved = RecoveryFolder::new(folder.path().join("unsaved"));
    eventually("the copy of the edit", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(2)
            && newest_recent(folder.path()) == copy
            && copy.is_some_and(|copy| unsaved.claim(&copy).is_ok())
    })
    .await;

    // A window restores the copy, then opens the file itself, which leaves the copy behind.
    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut window = Socket::own(&address, None).await;
    doc(&mut window, |ws| ws["recovered"] == true).await;
    let open = json!({"type": "open", "path": saved.display().to_string()});
    window.say(workspace(1, open)).await;
    assert_eq!(ack(&mut window, 1).await, json!({"status": "ok"}));
    assert_eq!(newest_recent(folder.path()), Some(saved));

    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut next = Socket::own(&address, None).await;
    let started = doc(&mut next, |_| true).await;
    assert_eq!(
        (&started["items"], &started["recovered"]),
        (&json!([]), &json!(false))
    );
}

#[cfg(unix)]
#[tokio::test]
async fn a_discarded_copy_that_cannot_be_deleted_is_not_restored_by_the_next_launch() {
    use std::os::unix::fs::PermissionsExt;
    let folder = tempfile::tempdir().unwrap();
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    let save = json!({"type": "save", "path": saved.display().to_string()});
    owner.say(workspace(2, save)).await;
    assert_eq!(ack(&mut owner, 2).await, json!({"status": "ok"}));
    owner.say(add_video(3)).await;
    owner.say(json!({"type": "leave"})).await;
    let unsaved = RecoveryFolder::new(folder.path().join("unsaved"));
    eventually("the copy of the edit", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(2)
            && newest_recent(folder.path()) == copy
            && copy.is_some_and(|copy| unsaved.claim(&copy).is_ok())
    })
    .await;

    // The copy can no longer be removed when it is discarded, and the window then closes.
    let mode = |mode| {
        let dir = folder.path().join("unsaved");
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(mode)).unwrap();
    };
    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut window = Socket::own(&address, None).await;
    doc(&mut window, |ws| ws["recovered"] == true).await;
    mode(0o500);
    if std::fs::File::create(folder.path().join("unsaved/probe")).is_ok() {
        mode(0o700);
        return; // running as a user the folder mode does not stop (root)
    }
    window.say(workspace(1, json!({"type": "discard"}))).await;
    let (outcome, _) = settle(&mut window, 1, |ws| ws["recovered"] == false).await;
    assert_eq!(outcome, json!({"status": "ok"}));
    window.say(json!({"type": "leave"})).await;
    // The closed window has let go of the copy, which is still there and not among the recents.
    let copy = copies(folder.path()).remove(0);
    eventually("the window to close", || unsaved.claim(&copy).is_ok()).await;
    mode(0o700);
    assert_eq!(newest_recent(folder.path()), Some(saved.clone()));

    let address = backend(folder.path(), Some(Launch::Restore)).await;
    let mut next = Socket::own(&address, None).await;
    let started = doc(&mut next, |_| true).await;
    assert_eq!(started["recovered"], false);
    assert_eq!(newest_recent(folder.path()), Some(saved));
}

#[tokio::test]
async fn a_copy_that_other_windows_pushed_out_of_the_recents_is_listed_again_before_it_is_left() {
    let folder = tempfile::tempdir().unwrap();
    let address = backend(folder.path(), None).await;
    let mut owner = Socket::own(&address, None).await;
    owner.say(add_video(1)).await;
    eventually("the recovery copy, listed", || {
        let copy = copies(folder.path()).pop();
        copy.as_deref().and_then(items_in) == Some(1) && newest_recent(folder.path()) == copy
    })
    .await;
    let copy = copies(folder.path()).remove(0);

    // Eight other workspaces opened elsewhere leave no room for the copy.
    for n in 0..8 {
        let other = folder.path().join(format!("other{n}{SUFFIX}"));
        recents(folder.path())
            .record(&other, u64::MAX / 2 + n)
            .unwrap();
    }
    assert!(
        recents(folder.path())
            .list()
            .iter()
            .all(|recent| recent.path != copy)
    );

    owner.say(workspace(2, json!({"type": "new"}))).await;
    assert_eq!(ack(&mut owner, 2).await, json!({"status": "ok"}));
    assert_eq!(newest_recent(folder.path()), Some(copy));
}
