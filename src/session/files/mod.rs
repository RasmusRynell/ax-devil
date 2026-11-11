//! The workspace file commands of the sessions, and the recovery copy that keeps unsaved work.
//!
//! A session that holds the files grant is attached: every 2 seconds, and when it ends, its modified workspace is
//! written to a recovery copy, and an unmodified one deletes the copy. New, Open, Save and Discard run one at a time
//! per session. Opening or starting over keeps the copy of the work it leaves behind; the next launch offers it.
//!
//! A command future can be dropped at any await. Bookkeeping (`synced`, `listed`, `copy`) is therefore made invalid
//! before the disk changes and valid only after it succeeded, so a cancelled step is repaired by the next sync.

mod operations;
mod recovery;

pub(super) use operations::sent;

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
use workspace::store::DocCommand;
use workspace_files::{Recent, Recents, RecoveryCopy, RecoveryFolder};

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
    /// Held by blocking file work until that work ends, wherever its caller goes: see [`SessionFiles::off`].
    disk: Arc<tokio::sync::Mutex<()>>,
}

impl SessionFiles {
    /// Runs blocking file work after any earlier work of this session has ended. The work owns the disk lock, so a
    /// caller that is dropped midway (its socket was cancelled) cannot let a later write overtake it.
    async fn off<T: Send + 'static>(&self, work: impl FnOnce() -> T + Send + 'static) -> T {
        let held = Arc::clone(&self.disk).lock_owned().await;
        tokio::task::spawn_blocking(move || {
            let done = work();
            drop(held);
            done
        })
        .await
        .expect("file work does not panic or get cancelled")
    }
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
    /// Rises, from 0, with every change this backend makes to the recent workspaces.
    recents: watch::Sender<u64>,
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
            recents: watch::channel(0).0,
        }
    }

    /// The count of changes this backend made to the recent workspaces, as they happen.
    pub fn recent_revisions(&self) -> watch::Receiver<u64> {
        self.recents.subscribe()
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
            disk: Arc::default(),
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
        let result = match &command {
            WorkspaceCommand::New => {
                self.replace(handle, &mut files, &command, Workspace::default(), None)
                    .await
            }
            WorkspaceCommand::Open { path } => {
                self.open(handle, &mut files, PathBuf::from(path), None)
                    .await
            }
            WorkspaceCommand::Save { path, replace } => {
                let path = path.clone().map(PathBuf::from);
                self.save(handle, &mut files, &command, path, replace.unwrap_or(false))
                    .await
            }
            WorkspaceCommand::Keep => match sent(&command, Workspace::default(), None) {
                Some(keep) => handle.command(keep).map(drop).map_err(Failure::from),
                None => Err(Refusal::Invalid.into()),
            },
            WorkspaceCommand::Discard => self.discard(handle, &mut files, &command).await,
            WorkspaceCommand::ExportBookmarks { path } => {
                self.export_bookmarks(handle, &files, PathBuf::from(path))
                    .await
            }
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Blocking work whose caller is dropped midway still finishes before the next work starts.
    #[tokio::test]
    async fn file_work_outlives_its_cancelled_caller_and_later_work_waits_for_it() {
        let state = SessionState::new(SessionId::generate(), true);
        let files = Arc::new(SessionFiles {
            rx: watch::channel(Arc::new(state)).1,
            copy: None,
            listed: false,
            synced: None,
            closed: false,
            disk: Arc::default(),
        });
        let order = Arc::new(Mutex::new(Vec::new()));
        let (started, running) = tokio::sync::oneshot::channel();
        let (release, gate) = std::sync::mpsc::channel::<()>();
        let (slow, log) = (Arc::clone(&files), Arc::clone(&order));
        let caller = tokio::spawn(async move {
            slow.off(move || {
                let _ = started.send(());
                let _ = gate.recv();
                log.lock().unwrap().push("first");
            })
            .await;
        });
        running.await.unwrap();
        caller.abort();
        let _ = caller.await;

        let log = Arc::clone(&order);
        let mut next = Box::pin(files.off(move || log.lock().unwrap().push("second")));
        // The first work still owns the disk lock, so the second cannot finish however long it is given.
        let waited = tokio::time::timeout(Duration::from_millis(100), &mut next).await;
        assert!(waited.is_err(), "the second work overtook the first");
        release.send(()).unwrap();
        next.await;
        assert_eq!(*order.lock().unwrap(), ["first", "second"]);
    }
}
