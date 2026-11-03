//! The recovery copy of a session's unsaved work, and the recent workspaces list it is kept in.

use super::{Key, SessionFiles, WorkspaceFiles};
use foundation::{ErrorCode, UserError};
use std::{
    path::Path,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use workspace_files::file_stem;
use workspace_store::WorkspaceState;

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| {
            u64::try_from(since.as_millis()).unwrap_or(u64::MAX)
        })
}

impl WorkspaceFiles {
    /// Brings the recovery copy up to date with `state`: written while modified, deleted when not. A failure is
    /// logged and the next sync tries again; keeping a modified workspace's copy is the one that reports it.
    pub(super) async fn sync(
        &self,
        files: &mut SessionFiles,
        state: &WorkspaceState,
    ) -> Result<(), UserError> {
        let Some(storage) = &self.storage else {
            return Ok(());
        };
        if files.closed {
            return Ok(());
        }
        let unkept = |reason: &dyn std::fmt::Display| {
            UserError::new(
                ErrorCode::SaveFailed,
                format!("Could not keep the unsaved changes: {reason}"),
            )
        };
        let file = state.file.as_ref().map(|file| file.as_path().to_owned());
        let key: Key = (state.workspace.clone(), file, state.modified());
        if files.synced.as_ref() == Some(&key) {
            return Ok(());
        }
        // Invalid while the disk is being changed, valid again only once every step has succeeded.
        files.synced = None;
        if key.2 {
            // The copy stays in `files` across every await: a command that is cancelled midway must not lose it.
            let copy = match &files.copy {
                Some(copy) => Arc::clone(copy),
                None => {
                    let (storage, stem) = (
                        Arc::clone(storage),
                        file_stem(state.workspace.name.as_deref()),
                    );
                    match files.off(move || storage.recovery.reserve(&stem)).await {
                        Ok(copy) => {
                            let copy = Arc::new(copy);
                            files.copy = Some(Arc::clone(&copy));
                            files.listed = false;
                            copy
                        }
                        Err(error) => {
                            tracing::warn!("Cannot start a recovery copy: {error}");
                            return Err(unkept(&error));
                        }
                    }
                }
            };
            let (workspace, recovers) = (key.0.clone(), key.1.clone());
            let path = copy.path().to_owned();
            let written = files
                .off(move || copy.write(&workspace, recovers.as_deref()))
                .await;
            if let Err(error) = written {
                tracing::warn!("Cannot write the recovery copy: {}", error.reason);
                return Err(error);
            }
            // Without its recent entry the copy would not be offered on the next launch, so try again next time.
            files.listed = files.listed || self.record(files, &path).await;
            if !files.listed {
                return Err(unkept(&"the recent workspaces cannot be updated"));
            }
        } else if let Some(copy) = files.copy.clone() {
            let path = copy.path().to_owned();
            // The entry goes first: a copy that stays on disk without one is only unlisted, never offered twice.
            let forgotten = self.forget(files, &path).await;
            files.listed = !forgotten;
            if !forgotten {
                return Ok(());
            }
            // Deleting unlinks the copy's lock file, so nothing may write through this handle afterwards.
            files.copy = None;
            let held = Arc::clone(&copy);
            if let Err(error) = files.off(move || copy.delete()).await {
                tracing::warn!("Cannot delete the recovery copy: {error}");
                files.copy = Some(held);
                return Ok(());
            }
        }
        files.synced = Some(key);
        Ok(())
    }

    /// Writes the session's last state and lets go of its copy, which stays on disk.
    pub(super) async fn close(&self, files: &mut SessionFiles) {
        let state = Arc::clone(&files.rx.borrow());
        let _ = self.sync(files, &state.doc.workspace).await;
        // The copy that outlives the window is what the next launch restores, so it is the newest recent again.
        if let Some(copy) = files.copy.take() {
            self.record(files, copy.path()).await;
        }
        files.closed = true;
    }

    /// Puts `path` first among the recent workspaces; false when that failed.
    pub(super) async fn record(&self, files: &SessionFiles, path: &Path) -> bool {
        let Some(storage) = &self.storage else {
            return true;
        };
        let (storage, path) = (Arc::clone(storage), path.to_owned());
        let done = files
            .off(move || storage.recents.record(&path, now_ms()))
            .await;
        done.inspect_err(|error| tracing::warn!("Cannot record a recent workspace: {error}"))
            .is_ok()
    }

    /// Removes `path` from the recent workspaces; false when that failed.
    pub(super) async fn forget(&self, files: &SessionFiles, path: &Path) -> bool {
        let Some(storage) = &self.storage else {
            return true;
        };
        let (storage, path) = (Arc::clone(storage), path.to_owned());
        let done = files.off(move || storage.recents.forget(&path)).await;
        done.inspect_err(|error| tracing::warn!("Cannot forget a recent workspace: {error}"))
            .is_ok()
    }
}
