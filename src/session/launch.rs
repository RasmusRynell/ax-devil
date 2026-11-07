//! What the backend was started with, or a later launch handed to it, until Local sessions take it.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    path::PathBuf,
    sync::{Mutex, PoisonError},
};
use workspace::{Workspace, WorkspaceItem};

/// What a Local session starts from.
pub enum Launch {
    /// Restore the unsaved work of the last window, if its recovery copy is the latest recent workspace.
    Restore,
    /// Open these items in the Untitled workspace.
    Items(Vec<WorkspaceItem>),
    /// Open the workspace file `path`, absolute.
    Open {
        /// The file.
        path: PathBuf,
        /// What it holds, when it was read to report a failure early; none for a recovery copy, which only the
        /// window that claims it may read.
        workspace: Option<Workspace>,
    },
}

/// A launch built by another process (a second `ax-devil local --video ...`, which knows its own working folder) as it
/// travels to the running backend.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum HandOff {
    /// The workspace file at this absolute path.
    Open(PathBuf),
    /// The items of this workspace document, as `workspace_files::to_json` wrote it.
    Items(Value),
}

impl HandOff {
    /// `launch` for another process to deliver; none for a restore, which only a start can ask for.
    pub fn of(launch: &Launch) -> Option<Self> {
        match launch {
            Launch::Restore => None,
            Launch::Open { path, .. } => Some(Self::Open(path.clone())),
            Launch::Items(items) => Some(Self::Items(workspace_files::to_json(&Workspace {
                items: items.clone(),
                ..Workspace::default()
            }))),
        }
    }

    /// The launch this stands for; the error says why the document is not one.
    pub fn into_launch(self) -> Result<Launch, String> {
        Ok(match self {
            Self::Open(path) => Launch::Open {
                path,
                workspace: None,
            },
            Self::Items(document) => Launch::Items(workspace_files::from_json(&document)?.items),
        })
    }
}

/// Launches waiting for Local sessions. Each session takes one, the latest first: a launch handed to the running
/// app is followed at once by the window that shows it, and the start's own (a restore) waits for the next window.
/// Nothing ties a launch to a window, so a window the user opens in between takes it.
#[derive(Default)]
pub struct LaunchQueue(Mutex<Vec<Launch>>);

impl LaunchQueue {
    /// A queue holding `launch`.
    pub fn new(launch: Option<Launch>) -> Self {
        Self(Mutex::new(launch.into_iter().collect()))
    }

    /// Adds `launch` ahead of what waits.
    pub fn push(&self, launch: Launch) {
        self.0
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(launch);
    }

    /// Hands back the latest launch; the one consumer.
    pub(super) fn take(&self) -> Option<Launch> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner).pop()
    }
}
