//! Other seats' cursors: a broadcast that skips on lag, outside the revisioned session state.

use crate::ParticipantId;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use tokio::sync::broadcast;

/// Cursor events a slow receiver may fall behind by before it starts missing them.
pub(crate) const CURSOR_BACKLOG: usize = 64;

/// A participant's cursor moved, or left the window (`at` is `None`).
#[derive(Clone, Debug, PartialEq)]
pub struct CursorEvent<C> {
    /// Whose cursor.
    pub participant: ParticipantId,
    /// Where it is now.
    pub at: Option<C>,
}

/// A seat's feed of other seats' cursors. A seat with "show" off receives none, and a slow reader loses the oldest
/// events and nothing else.
pub struct Cursors<C> {
    pub(crate) rx: broadcast::Receiver<CursorEvent<C>>,
    pub(crate) you: ParticipantId,
    pub(crate) show: Arc<AtomicBool>,
}

impl<C: Clone> Cursors<C> {
    /// The next cursor event of another seat, or `None` once the session has ended.
    pub async fn recv(&mut self) -> Option<CursorEvent<C>> {
        loop {
            match self.rx.recv().await {
                Ok(event) if event.participant != self.you && self.show.load(Ordering::Relaxed) => {
                    return Some(event);
                }
                Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {}
                Err(broadcast::error::RecvError::Closed) => return None,
            }
        }
    }
}
