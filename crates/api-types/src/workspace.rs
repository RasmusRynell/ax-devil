//! Wire types for the workspace routes and events.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The workspace and what is known about each item: the body of `GET /api/workspace` and of every `Workspace` event.
#[derive(Serialize, Deserialize, TS)]
pub struct WorkspaceState {
    /// The user's name for the workspace; `null` is Untitled.
    pub name: Option<String>,
    /// Every item in the order it was added.
    pub items: Vec<ItemState>,
}

/// One workspace item and how far it has resolved.
#[derive(Serialize, Deserialize, TS)]
pub struct ItemState {
    /// Names the item.
    pub id: String,
    /// The name the item's row shows.
    pub label: String,
    /// Whether a viewer should open for the item once it resolves. Stays set; the frontend remembers which
    /// viewers it has opened.
    pub open: bool,
    /// Whether the item has Content yet.
    pub resolution: ResolutionState,
}

/// How far an item has resolved.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum ResolutionState {
    /// Resolving has not finished.
    Pending,
    /// The item has Content a viewer can open.
    Resolved {
        /// What the item resolved to.
        content: ContentInfo,
    },
    /// The item stays in the workspace without Content.
    Failed {
        /// What is wrong, in words for the user.
        reason: String,
        /// What it is about, such as the file path.
        location: String,
    },
}

/// Content by kind. Viewers register by kind.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum ContentInfo {
    /// A recorded video.
    SeekableVideo {
        /// Names the Content; the playback socket opens it by this id.
        id: String,
        /// The name the viewer shows.
        display_name: String,
    },
}

/// What the user asked to add; the intake turns each into an item.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum NewItem {
    /// A video file.
    Video {
        /// The file; a relative path is relative to the backend's working folder.
        path: String,
    },
}

/// The body of `POST /api/workspace/items`. The intake checks every entry first: if any is refused, nothing is added.
#[derive(Serialize, Deserialize, TS)]
pub struct AddItemsRequest {
    /// What to add, in order.
    pub items: Vec<NewItem>,
    /// Whether a viewer should open for each item once it resolves.
    pub open: bool,
}

/// What `GET /api/events` streams, one per server-sent event, starting with the current state on connect.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum ServerEvent {
    /// The workspace changed; this is the whole new state.
    Workspace(WorkspaceState),
}
