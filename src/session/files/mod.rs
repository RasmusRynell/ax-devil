//! The workspace file commands of the sessions, and the recovery copy that keeps unsaved work.
//!
//! A session that holds the files grant is attached: every 2 seconds, and when it ends, its modified workspace is
//! written to a recovery copy, and an unmodified one deletes the copy. New, Open, Save and Discard run one at a time
//! per session. Opening or starting over keeps the copy of the work it leaves behind; the next launch offers it.

mod operations;
mod recovery;

use super::launch::Launch;
use api_types::{AckOutcome, Audience, WorkspaceCommand};
use foundation::UserError;
use sessions::{
    SessionId,
    session::{Command, Handle, Refusal, SessionState},
};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::Duration,
};
use tokio::{
    sync::{OwnedMutexGuard, watch},
    time::{MissedTickBehavior, interval},
};
use workspace::Workspace;
use workspace_files::{Recent, Recents, RecoveryCopy, RecoveryFolder};
use workspace_store::DocCommand;

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
    copy: Option<Arc<RecoveryCopy>>,
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
                let _ = this.sync(&mut files, &state.doc.workspace).await;
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
            WorkspaceCommand::Save { path, replace } => {
                let path = path.map(PathBuf::from);
                self.save(handle, &mut files, path, replace.unwrap_or(false))
                    .await
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
}
