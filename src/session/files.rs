//! The workspace file commands of the sessions, and the recovery copy that keeps unsaved work.
//!
//! A session that holds the files grant is attached: every 2 seconds, and when it ends, its modified workspace is
//! written to a recovery copy, and an unmodified one deletes the copy. New, Open, Save and Discard run one at a time
//! per session. Opening or starting over keeps the copy of the work it leaves behind; the next launch offers it.

use super::launch::Launch;
use api_types::{AckOutcome, Audience, WorkspaceCommand};
use foundation::{ErrorCode, HostPath, UserError};
use sessions::{
    SessionId,
    session::{Command, Handle, Refusal, SessionState},
};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::{OwnedMutexGuard, watch},
    time::{MissedTickBehavior, interval},
};
use workspace::Workspace;
use workspace_files::{Claim, Recent, Recents, RecoveryCopy, RecoveryFolder, file_stem};
use workspace_store::{DocCommand, Saved, WorkspaceState};

/// How often a modified workspace is written to its recovery copy.
const SYNC_EVERY: Duration = Duration::from_secs(2);

/// Where recovery copies and recent workspaces are kept.
pub struct WorkspaceStorage {
    /// The recent workspaces list.
    pub recents: Recents,
    /// The recovery copies.
    pub recovery: RecoveryFolder,
}

/// What the last sync wrote: the workspace, its file and whether it was modified.
type Key = (Workspace, Option<PathBuf>, bool);

/// One session's files, held while an operation or a sync runs.
struct SessionFiles {
    rx: watch::Receiver<Arc<SessionState>>,
    /// The recovery copy this session holds, if any.
    copy: Option<RecoveryCopy>,
    /// Whether the copy is in the recent workspaces.
    listed: bool,
    synced: Option<Key>,
    /// The session ended or the backend stopped; nothing more is written.
    closed: bool,
}

/// Why a command did nothing.
enum Failure {
    Refused(Refusal),
    Failed(UserError),
}

impl From<Refusal> for Failure {
    fn from(refusal: Refusal) -> Self {
        Self::Refused(refusal)
    }
}

impl From<UserError> for Failure {
    fn from(error: UserError) -> Self {
        Self::Failed(error)
    }
}

/// Every session's workspace files in one backend.
pub struct WorkspaceFiles {
    /// `None` keeps no recovery copies and no recent workspaces.
    storage: Option<Arc<WorkspaceStorage>>,
    sessions: Mutex<HashMap<SessionId, Arc<tokio::sync::Mutex<SessionFiles>>>>,
}

/// Runs blocking file work off the async workers.
async fn off<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> T {
    tokio::task::spawn_blocking(work)
        .await
        .expect("file work does not panic or get cancelled")
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| {
            u64::try_from(since.as_millis()).unwrap_or(u64::MAX)
        })
}

fn name_of(path: &Path) -> String {
    path.file_name()
        .map_or_else(String::new, |name| name.to_string_lossy().into_owned())
}

impl WorkspaceFiles {
    /// Files kept in `storage`, or none.
    pub fn new(storage: Option<WorkspaceStorage>) -> Self {
        Self {
            storage: storage.map(Arc::new),
            sessions: Mutex::default(),
        }
    }

    /// The recent workspaces, newest first. Blocks.
    pub fn recent(&self) -> Vec<Recent> {
        self.storage
            .as_ref()
            .map_or_else(Vec::new, |storage| storage.recents.list())
    }

    /// Starts keeping the recovery copy of `session`, whose owner holds the files grant, until it ends.
    pub fn attach(self: &Arc<Self>, session: SessionId, rx: watch::Receiver<Arc<SessionState>>) {
        let files = Arc::new(tokio::sync::Mutex::new(SessionFiles {
            rx: rx.clone(),
            copy: None,
            listed: false,
            synced: None,
            closed: false,
        }));
        self.sessions
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(session, Arc::clone(&files));
        let this = Arc::clone(self);
        let mut rx = rx;
        tokio::spawn(async move {
            let mut tick = interval(SYNC_EVERY);
            tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                // A dropped sender ends the session as well.
                let ended = tokio::select! {
                    _ = tick.tick() => false,
                    _ = rx.wait_for(|state| state.ended.is_some()) => true,
                };
                let mut files = files.lock().await;
                if files.closed {
                    return;
                }
                if ended {
                    this.close(&mut files).await;
                    this.sessions
                        .lock()
                        .unwrap_or_else(PoisonError::into_inner)
                        .remove(&session);
                    return;
                }
                let state = Arc::clone(&rx.borrow());
                this.sync(&mut files, &state.doc.workspace).await;
            }
        });
    }

    /// Writes every session's last state and lets go of the copies; called once the server has stopped.
    pub async fn flush_all(&self) {
        let all: Vec<_> = self
            .sessions
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .drain()
            .collect();
        for (_, files) in all {
            self.close(&mut *files.lock().await).await;
        }
    }

    /// Runs `command` for the seat of `handle`.
    pub async fn run(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        command: WorkspaceCommand,
    ) -> AckOutcome {
        let Some(mut files) = self.lock(handle).await else {
            return AckOutcome::Refused {
                code: Refusal::NotAllowed,
            };
        };
        let result = match command {
            WorkspaceCommand::New => {
                self.replace(handle, &mut files, Workspace::default(), None)
                    .await
            }
            WorkspaceCommand::Open { path } => {
                self.open(handle, &mut files, PathBuf::from(path), None)
                    .await
            }
            WorkspaceCommand::Save { path } => {
                self.save(handle, &mut files, path.map(PathBuf::from)).await
            }
            WorkspaceCommand::Keep => handle
                .command(Command::Content(DocCommand::Keep))
                .map(drop)
                .map_err(Failure::from),
            WorkspaceCommand::Discard => self.discard(handle, &mut files).await,
        };
        match result {
            Ok(()) => AckOutcome::Ok,
            Err(Failure::Refused(code)) => AckOutcome::Refused { code },
            Err(Failure::Failed(error)) => AckOutcome::Failed {
                error: api_types::project(&error, Audience::HOST),
            },
        }
    }

    /// Starts the first Local session from `launch`. A restore that cannot happen is only logged.
    pub async fn launch(&self, handle: &Handle<api_types::CursorPoint>, launch: Launch) {
        let Some(mut files) = self.lock(handle).await else {
            return;
        };
        let result = match launch {
            Launch::Items(items) => handle
                .command(Command::Content(DocCommand::AddItems { items, open: true }))
                .map(drop)
                .map_err(Failure::from),
            Launch::Open { path, workspace } => {
                self.open(handle, &mut files, path, workspace).await
            }
            Launch::Restore => {
                let latest = self.recent().into_iter().next();
                let restorable = self
                    .storage
                    .as_ref()
                    .zip(latest)
                    .filter(|(storage, recent)| storage.recovery.contains(&recent.path));
                match restorable {
                    Some((_, recent)) => self.restore(handle, &mut files, recent.path).await,
                    None => Ok(()),
                }
            }
        };
        match result {
            Ok(()) => {}
            Err(Failure::Refused(code)) => tracing::debug!("The launch was refused: {code:?}"),
            Err(Failure::Failed(error)) => {
                tracing::info!("The launch did not open: {}", error.reason)
            }
        }
    }

    async fn lock(
        &self,
        handle: &Handle<api_types::CursorPoint>,
    ) -> Option<OwnedMutexGuard<SessionFiles>> {
        let files = self
            .sessions
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(&handle.session())
            .cloned()?;
        Some(files.lock_owned().await)
    }

    /// Makes `workspace` current, keeping the copy of the work it replaces.
    async fn replace(
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
        self.sync(files, &handle.state().doc.workspace).await;
        files.copy = None;
        files.listed = false;
        files.synced = None;
        let replace = DocCommand::Replace {
            workspace,
            file: file.map(HostPath::new),
            saved,
        };
        handle.command(Command::Content(replace))?;
        Ok(())
    }

    /// Opens the workspace file `path`, read already when `loaded`. A recovery copy is claimed first and read only
    /// once it is this window's.
    async fn open(
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
            None => match off(move || workspace_files::load(&read)).await {
                Ok(workspace) => workspace,
                Err(error) => {
                    self.forget(&path).await;
                    return Err(error.into());
                }
            },
        };
        self.replace(handle, files, workspace, Some(path.clone()))
            .await?;
        self.record(&path).await;
        Ok(())
    }

    /// Takes over the recovery copy `path`: modified until saved, with Keep and Discard on offer.
    async fn restore(
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
                self.forget(&path).await;
                return Err(error.into());
            }
        };
        let (claimed, read) = off(move || {
            let read = claimed.read();
            (claimed, read)
        })
        .await;
        let (workspace, recovers) = match read {
            Ok(read) => read,
            Err(error) => {
                self.forget(&path).await;
                return Err(error.into());
            }
        };
        let saved = Saved::Recovered { offer: true };
        self.swap(handle, files, workspace.clone(), recovers.clone(), saved)
            .await?;
        files.synced = Some((workspace, recovers, true));
        files.listed = true;
        let copy = claimed.path().to_owned();
        files.copy = Some(claimed);
        self.record(&copy).await;
        Ok(())
    }

    async fn save(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
        path: Option<PathBuf>,
    ) -> Result<(), Failure> {
        let state = handle.state();
        let doc = &state.doc.workspace;
        let own = doc.file.as_ref().map(|file| file.as_path().to_owned());
        // Without a file, the client runs Save As.
        let target = path.or(own).ok_or(Refusal::Invalid)?;
        // A recovery copy is written only by the window that holds it.
        if let Some(storage) = &self.storage
            && storage.recovery.reaches(&target)
        {
            return Err(Refusal::Invalid.into());
        }
        let snapshot = doc.workspace.clone();
        let (written, to) = (snapshot.clone(), target.clone());
        off(move || workspace_files::save(&written, &to)).await?;
        let saved = DocCommand::MarkSaved {
            workspace: snapshot,
            file: HostPath::new(&target),
        };
        handle.command(Command::Content(saved))?;
        self.sync(files, &handle.state().doc.workspace).await;
        self.record(&target).await;
        Ok(())
    }

    /// Back to the saved file, or an empty Untitled workspace, dropping the changes and their copy.
    async fn discard(
        &self,
        handle: &Handle<api_types::CursorPoint>,
        files: &mut SessionFiles,
    ) -> Result<(), Failure> {
        let file = handle
            .state()
            .doc
            .workspace
            .file
            .as_ref()
            .map(|file| file.as_path().to_owned());
        let workspace = match file.clone() {
            Some(path) => off(move || workspace_files::load(&path)).await?,
            None => Workspace::default(),
        };
        let saved = DocCommand::Replace {
            workspace: workspace.clone(),
            file: file.clone().map(HostPath::new),
            saved: Saved::As(workspace),
        };
        handle.command(Command::Content(saved))?;
        // Unmodified now, so the sync deletes the copy.
        self.sync(files, &handle.state().doc.workspace).await;
        if let Some(file) = file {
            self.record(&file).await;
        }
        Ok(())
    }

    /// Brings the recovery copy up to date with `state`: written while modified, deleted when not. A failure is
    /// logged and the next sync tries again.
    async fn sync(&self, files: &mut SessionFiles, state: &WorkspaceState) {
        let Some(storage) = &self.storage else {
            return;
        };
        if files.closed {
            return;
        }
        let file = state.file.as_ref().map(|file| file.as_path().to_owned());
        let key: Key = (state.workspace.clone(), file, state.modified());
        if files.synced.as_ref() == Some(&key) {
            return;
        }
        if key.2 {
            let copy = match files.copy.take() {
                Some(copy) => copy,
                None => {
                    let (storage, stem) = (
                        Arc::clone(storage),
                        file_stem(state.workspace.name.as_deref()),
                    );
                    match off(move || storage.recovery.reserve(&stem)).await {
                        Ok(copy) => copy,
                        Err(error) => {
                            tracing::warn!("Cannot start a recovery copy: {error}");
                            return;
                        }
                    }
                }
            };
            let (workspace, recovers) = (key.0.clone(), key.1.clone());
            let (copy, written) = off(move || {
                let written = copy.write(&workspace, recovers.as_deref());
                (copy, written)
            })
            .await;
            let path = copy.path().to_owned();
            files.copy = Some(copy);
            if let Err(error) = written {
                tracing::warn!("Cannot write the recovery copy: {}", error.reason);
                return;
            }
            // Without its recent entry the copy would not be offered on the next launch, so try again next time.
            files.listed = files.listed || self.record(&path).await;
            if !files.listed {
                return;
            }
        } else if let Some(copy) = files.copy.take() {
            let path = copy.path().to_owned();
            // The entry goes first: a copy that stays on disk without one is only unlisted, never offered twice.
            let forgotten = self.forget(&path).await;
            files.listed = !forgotten;
            let (copy, deleted) = off(move || {
                let deleted = if forgotten { copy.delete() } else { Ok(()) };
                (copy, deleted)
            })
            .await;
            let failed = deleted.is_err() || !forgotten;
            if let Err(error) = deleted {
                tracing::warn!("Cannot delete the recovery copy: {error}");
            }
            if failed {
                files.copy = Some(copy);
                return;
            }
        }
        files.synced = Some(key);
    }

    /// Writes the session's last state and lets go of its copy, which stays on disk.
    async fn close(&self, files: &mut SessionFiles) {
        let state = Arc::clone(&files.rx.borrow());
        self.sync(files, &state.doc.workspace).await;
        // The copy that outlives the window is what the next launch restores, so it is the newest recent again.
        if let Some(copy) = files.copy.take() {
            self.record(copy.path()).await;
        }
        files.closed = true;
    }

    /// Puts `path` first among the recent workspaces; false when that failed.
    async fn record(&self, path: &Path) -> bool {
        let Some(storage) = &self.storage else {
            return true;
        };
        let (storage, path) = (Arc::clone(storage), path.to_owned());
        let done = off(move || storage.recents.record(&path, now_ms())).await;
        done.inspect_err(|error| tracing::warn!("Cannot record a recent workspace: {error}"))
            .is_ok()
    }

    /// Removes `path` from the recent workspaces; false when that failed.
    async fn forget(&self, path: &Path) -> bool {
        let Some(storage) = &self.storage else {
            return true;
        };
        let (storage, path) = (Arc::clone(storage), path.to_owned());
        let done = off(move || storage.recents.forget(&path)).await;
        done.inspect_err(|error| tracing::warn!("Cannot forget a recent workspace: {error}"))
            .is_ok()
    }
}
