//! The items `ax-devil local --video` was started with, until the first Local session takes them.

use std::sync::{Mutex, PoisonError};
use workspace::WorkspaceItem;

/// Items waiting for a Session. Whichever Local session asks first takes all of them, so a second `local --video`
/// reaches whichever window connects first; nothing ties an item to a window.
#[derive(Default)]
pub struct LaunchQueue(Mutex<Vec<WorkspaceItem>>);

impl LaunchQueue {
    /// A queue holding `items`.
    pub fn new(items: Vec<WorkspaceItem>) -> Self {
        Self(Mutex::new(items))
    }

    /// Empties the queue and hands back what it held; the one consumer.
    pub(super) fn take(&self) -> Vec<WorkspaceItem> {
        std::mem::take(&mut *self.0.lock().unwrap_or_else(PoisonError::into_inner))
    }
}
