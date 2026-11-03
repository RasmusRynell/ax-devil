//! What the backend was started with, until the first Local session takes it.

use std::{
    path::PathBuf,
    sync::{Mutex, PoisonError},
};
use workspace::{Workspace, WorkspaceItem};

/// What the first Local session starts from.
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

/// A launch waiting for a Session. Whichever Local session asks first takes it, so a second `local --video` reaches
/// whichever window connects first; nothing ties it to a window.
#[derive(Default)]
pub struct LaunchQueue(Mutex<Option<Launch>>);

impl LaunchQueue {
    /// A queue holding `launch`.
    pub fn new(launch: Option<Launch>) -> Self {
        Self(Mutex::new(launch))
    }

    /// Empties the queue and hands back what it held; the one consumer.
    pub(super) fn take(&self) -> Option<Launch> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner).take()
    }
}
