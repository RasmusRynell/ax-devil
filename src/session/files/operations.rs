//! New, Open, Save and Discard: the operations that replace the workspace or write its file.

use super::{Failure, SessionFiles, WorkspaceFiles, name_of};
use api_types::WorkspaceCommand;
use foundation::{ErrorCode, HostPath, UserError};
use sessions::session::{Command, Handle, Refusal};
use std::{path::PathBuf, sync::Arc};
use workspace::Workspace;
use workspace::store::{DocCommand, Saved};
use workspace_files::{Claim, resolved};

/// The Session command that `command` sends once its file work is done, over the workspace that work read or wrote and
/// the file it names; `None` for a command that sends none. The one mapping from a client's workspace command to the
/// Session command, so the scope the wire asks of it is checked against what the Session will ask (`Command::needs`).
/// Restoring a recovery copy builds its own `Replace { saved: Recovered }`, which `content_scope` treats as any `Replace`.
pub(in crate::session) fn sent(
    command: &WorkspaceCommand,
    workspace: Workspace,
    file: Option<HostPath>,
) -> Option<Command> {
    Some(Command::Content(match command {
        WorkspaceCommand::New | WorkspaceCommand::Open { .. } | WorkspaceCommand::Discard => {
            DocCommand::Replace {
                saved: Saved::As(workspace.clone()),
                workspace,
                file,
            }
        }
        WorkspaceCommand::Save { .. } => DocCommand::MarkSaved {
            workspace,
            file: file?,
        },
        WorkspaceCommand::Keep => DocCommand::Keep,
        WorkspaceCommand::ExportBookmarks { .. } => return None,
    }))
}

impl WorkspaceFiles {
    /// Makes `workspace` current for `command`, keeping the copy of the work it replaces.
    pub(super) async fn replace(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        command: &WorkspaceCommand,
        workspace: Workspace,
        file: Option<PathBuf>,
    ) -> Result<(), Failure> {
        let replace = sent(command, workspace, file.map(HostPath::new)).ok_or(Refusal::Invalid)?;
        self.swap(handle, files, replace).await
    }

    /// Sends `replace`, a command that replaces the workspace, after keeping the copy of the work it replaces.
    async fn swap(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        replace: Command,
    ) -> Result<(), Failure> {
        // Recovery failures are logged by sync; they do not prevent the requested workspace switch.
        let outgoing = handle.state();
        let _ = self.sync(files, &outgoing.doc.workspace).await;
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
            self.record(files, copy.path(), recovers).await;
        }
        // A refusal changes nothing; after the replacement, nothing awaits before the copy is let go of.
        handle.command(replace)?;
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
        // Only selects the kind for `sent`: its path is ignored, and the launch path has no real command to pass.
        let open = WorkspaceCommand::Open {
            path: path.to_string_lossy().into_owned(),
        };
        self.replace(handle, files, &open, workspace, Some(path.clone()))
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
        let replace = Command::Content(DocCommand::Replace {
            workspace: workspace.clone(),
            file: recovers.clone().map(HostPath::new),
            saved,
        });
        self.swap(handle, files, replace).await?;
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
        command: &WorkspaceCommand,
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
        let saved =
            sent(command, snapshot, Some(HostPath::new(&target))).ok_or(Refusal::Invalid)?;
        handle.command(saved)?;
        let _ = self.sync(files, &handle.state().doc.workspace).await;
        self.record(files, &target, None).await;
        Ok(())
    }

    /// Writes every bookmark to the CSV file `path`. Any other kind of file is refused, so a seat cannot use the export to
    /// overwrite a file that is not a CSV.
    pub(super) async fn export_bookmarks(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &SessionFiles,
        path: PathBuf,
    ) -> Result<(), Failure> {
        if !path
            .extension()
            .is_some_and(|found| found.eq_ignore_ascii_case("csv"))
        {
            return Err(
                UserError::new(ErrorCode::InvalidPath, "The file must end in .csv")
                    .at(HostPath::new(&path))
                    .into(),
            );
        }
        let text = workspace::store::bookmarks_csv(&handle.state().doc.workspace);
        files
            .off(move || workspace_files::write_text(&path, &text))
            .await?;
        Ok(())
    }

    /// Back to the saved file, or an empty Untitled workspace, dropping the changes and their copy. A saved file that
    /// cannot be reopened fails the Discard and keeps both the edits and their copy.
    pub(super) async fn discard(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        command: &WorkspaceCommand,
    ) -> Result<(), Failure> {
        let file = handle
            .state()
            .doc
            .workspace
            .file
            .as_ref()
            .map(|file| file.as_path().to_owned());
        let workspace = match file.clone() {
            Some(path) => files.off(move || workspace_files::load(&path)).await?,
            None => Workspace::default(),
        };
        let saved =
            sent(command, workspace, file.clone().map(HostPath::new)).ok_or(Refusal::Invalid)?;
        handle.command(saved)?;
        // Unmodified now, so the sync deletes the copy.
        let _ = self.sync(files, &handle.state().doc.workspace).await;
        if let Some(file) = file {
            self.record(files, &file, None).await;
        }
        Ok(())
    }
}
