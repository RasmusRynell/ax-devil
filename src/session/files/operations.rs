//! New, Open, Save and Discard: the operations that replace the workspace or write its file.

use super::{Failure, SessionFiles, WorkspaceFiles, name_of};
use foundation::{ErrorCode, HostPath, UserError};
use sessions::session::{Command, Handle, Refusal};
use std::{path::PathBuf, sync::Arc};
use workspace::Workspace;
use workspace_files::{Claim, resolved};
use workspace_store::{DocCommand, Saved};

impl WorkspaceFiles {
    /// Makes `workspace` current, keeping the copy of the work it replaces.
    pub(super) async fn replace(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        workspace: Workspace,
        file: Option<PathBuf>,
    ) -> Result<(), Failure> {
        let saved = Saved::As(workspace.clone());
        self.swap(handle, files, workspace, file, saved).await
    }

    async fn swap(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        workspace: Workspace,
        file: Option<PathBuf>,
        saved: Saved,
    ) -> Result<(), Failure> {
        // Unsaved work that cannot be kept stays where it is, so the user can save it elsewhere.
        let outgoing = handle.state();
        self.sync(files, &outgoing.doc.workspace).await?;
        // Other windows can push the copy out of the recents, so it is put back rather than trusted to be there.
        if let Some(copy) = files.copy.clone()
            && outgoing.doc.workspace.modified()
        {
            let recovers = outgoing
                .doc
                .workspace
                .file
                .as_ref()
                .map(|file| file.as_path());
            if !self.record(files, copy.path(), recovers).await {
                let reason =
                    "Could not keep the unsaved changes: the recent workspaces cannot be updated";
                return Err(UserError::new(ErrorCode::SaveFailed, reason).into());
            }
        }
        let replace = DocCommand::Replace {
            workspace,
            file: file.map(HostPath::new),
            saved,
        };
        // A refusal changes nothing; after the replacement, nothing awaits before the copy is let go of.
        handle.command(Command::Content(replace))?;
        files.copy = None;
        files.listed = false;
        files.synced = None;
        Ok(())
    }

    /// Opens the workspace file `path`, read already when `loaded`. A recovery copy is claimed first and read only
    /// once it is this window's.
    pub(super) async fn open(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        path: PathBuf,
        loaded: Option<Workspace>,
    ) -> Result<(), Failure> {
        if self
            .storage
            .as_ref()
            .is_some_and(|storage| storage.recovery.contains(&path))
        {
            return self.restore(handle, files, path).await;
        }
        let read = path.clone();
        let workspace = match loaded {
            Some(workspace) => workspace,
            None => match files.off(move || workspace_files::load(&read)).await {
                Ok(workspace) => workspace,
                Err(error) => {
                    self.forget(files, &path).await;
                    return Err(error.into());
                }
            },
        };
        self.replace(handle, files, workspace, Some(path.clone()))
            .await?;
        self.record(files, &path, None).await;
        Ok(())
    }

    /// Takes over the recovery copy `path`: modified until saved, with Keep and Discard on offer.
    pub(super) async fn restore(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        path: PathBuf,
    ) -> Result<(), Failure> {
        let Some(storage) = &self.storage else {
            return Ok(());
        };
        // Reopening the copy this window holds changes nothing.
        if files.copy.as_ref().is_some_and(|copy| copy.is_at(&path)) {
            return Ok(());
        }
        let claimed = match storage.recovery.claim(&path) {
            Ok(copy) => copy,
            Err(Claim::InUse) => {
                let reason = format!("{} is open in another window.", name_of(&path));
                return Err(UserError::new(ErrorCode::WorkspaceInUse, reason)
                    .at(HostPath::new(&path))
                    .into());
            }
            Err(Claim::Failed(error)) => {
                self.forget(files, &path).await;
                return Err(error.into());
            }
        };
        let (claimed, read) = files
            .off(move || {
                let read = claimed.read();
                (claimed, read)
            })
            .await;
        let (workspace, recovers) = match read {
            Ok(read) => read,
            Err(error) => {
                self.forget(files, &path).await;
                return Err(error.into());
            }
        };
        let saved = Saved::Recovered { offer: true };
        self.swap(handle, files, workspace.clone(), recovers.clone(), saved)
            .await?;
        let copy = claimed.path().to_owned();
        files.copy = Some(Arc::new(claimed));
        // Marked synced only once listed: a command cancelled before that leaves the next sync to finish the job.
        files.listed = self.record(files, &copy, recovers.as_deref()).await;
        if files.listed {
            files.synced = Some((workspace, recovers, true));
        }
        Ok(())
    }

    pub(super) async fn save(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        path: Option<PathBuf>,
        replace: bool,
    ) -> Result<(), Failure> {
        let state = handle.state();
        let doc = &state.doc.workspace;
        let own = doc.file.as_ref().map(|file| file.as_path().to_owned());
        // Without a file, the client runs Save As.
        let target = path.or(own).ok_or(Refusal::Invalid)?;
        // A recovery copy is written only by the window that holds it.
        if let Some(storage) = &self.storage
            && storage.recovery.contains(&target)
        {
            return Err(Refusal::Invalid.into());
        }
        // Save As onto another existing file asks first; the workspace's own file is always its to write.
        let own_file = |file: &HostPath| resolved(file.as_path()) == resolved(&target);
        if !replace && target.exists() && !doc.file.as_ref().is_some_and(own_file) {
            let reason = format!("{} already exists.", name_of(&target));
            let error =
                UserError::new(ErrorCode::WorkspaceExists, reason).at(HostPath::new(&target));
            return Err(error.into());
        }
        let snapshot = doc.workspace.clone();
        let (written, to) = (snapshot.clone(), target.clone());
        files
            .off(move || workspace_files::save(&written, &to))
            .await?;
        let saved = DocCommand::MarkSaved {
            workspace: snapshot,
            file: HostPath::new(&target),
        };
        handle.command(Command::Content(saved))?;
        let _ = self.sync(files, &handle.state().doc.workspace).await;
        self.record(files, &target, None).await;
        Ok(())
    }

    /// Writes every bookmark to the CSV file `path`.
    pub(super) async fn export_bookmarks(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &SessionFiles,
        path: PathBuf,
    ) -> Result<(), Failure> {
        let text = workspace_store::bookmarks_csv(&handle.state().doc.workspace);
        files
            .off(move || workspace_files::write_text(&path, &text))
            .await?;
        Ok(())
    }

    /// Back to the saved file, or an empty Untitled workspace, dropping the changes and their copy.
    pub(super) async fn discard(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
    ) -> Result<(), Failure> {
        let mut file = handle
            .state()
            .doc
            .workspace
            .file
            .as_ref()
            .map(|file| file.as_path().to_owned());
        let mut workspace = Workspace::default();
        if let Some(path) = file.clone() {
            // A file that is gone or unreadable cannot be gone back to, but the changes are still dropped.
            match files.off(move || workspace_files::load(&path)).await {
                Ok(loaded) => workspace = loaded,
                Err(error) => {
                    tracing::warn!("Discard starts empty: {}", error.reason);
                    file = None;
                }
            }
        }
        let saved = DocCommand::Replace {
            workspace: workspace.clone(),
            file: file.clone().map(HostPath::new),
            saved: Saved::As(workspace),
        };
        handle.command(Command::Content(saved))?;
        // Unmodified now, so the sync deletes the copy.
        let _ = self.sync(files, &handle.state().doc.workspace).await;
        if let Some(file) = file {
            self.record(files, &file, None).await;
        }
        Ok(())
    }
}
