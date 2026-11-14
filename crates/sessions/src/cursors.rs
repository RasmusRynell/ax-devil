//! Other seats' cursors: a broadcast that skips on lag, outside the revisioned session state.

use crate::ParticipantId;
use serde::{Deserialize, Serialize};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use tokio::sync::broadcast;
use ts_rs::TS;

/// Cursor events a slow receiver may fall behind by before it starts missing them.
pub(crate) const CURSOR_BACKLOG: usize = 64;

/// Longest element id or scope a cursor may carry, in characters.
pub const CURSOR_NAME_MAX: usize = 64;

/// The part of the window a cursor position belongs to.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum CursorRegion {
    /// The title bar.
    TitleBar,
    /// The activity bar.
    ActivityBar,
    /// The sidebar.
    Sidebar,
    /// The central area.
    Main,
}

/// A position inside one element, which receivers find by its id.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq)]
pub struct CursorElement {
    /// The element's `data-cursor-id`, at most [`CURSOR_NAME_MAX`] characters.
    pub id: String,
    /// Left to right, a fraction of the element's width.
    pub x: f32,
    /// Top to bottom, a fraction of the element's height.
    pub y: f32,
}

/// A cursor position. Every `x` and `y` is a fraction in `[0, 1]`, clamped by the backend, so windows of any size
/// agree.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq)]
pub struct CursorPoint {
    /// What `x` and `y` are measured in.
    pub region: CursorRegion,
    /// What the region shows right now: the sidebar view id, or the open viewer's id or `welcome`.
    pub scope: String,
    /// Left to right, a fraction of the region's width.
    pub x: f32,
    /// Top to bottom, a fraction of the region's height.
    pub y: f32,
    /// The element under the pointer, when it has an id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub element: Option<CursorElement>,
}

/// A participant's cursor moved, or left the window (`at` is `None`).
#[derive(Clone, Debug, PartialEq)]
pub struct CursorEvent {
    /// Whose cursor.
    pub participant: ParticipantId,
    /// Where it is now.
    pub at: Option<CursorPoint>,
}

/// A seat's feed of other seats' cursors. A seat with "show" off receives none, and a slow reader loses the oldest
/// events and nothing else.
pub struct Cursors {
    pub(crate) rx: broadcast::Receiver<CursorEvent>,
    pub(crate) you: ParticipantId,
    pub(crate) show: Arc<AtomicBool>,
}

impl Cursors {
    /// The next cursor event of another seat, or `None` once the session has ended.
    pub async fn recv(&mut self) -> Option<CursorEvent> {
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
