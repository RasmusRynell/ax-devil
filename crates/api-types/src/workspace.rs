//! The workspace as the Session socket carries it: the Doc's wire form and the items a client asks to add.

use crate::ErrorWire;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The workspace and what is known about each item, as one audience may see it.
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
    /// The item stays in the workspace without Content; this is why.
    Failed(ErrorWire),
}

/// Content by kind. Viewers register by kind.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum ContentInfo {
    /// A recorded video.
    SeekableVideo {
        /// Names the Content; a ticket request names the stream by this id and `generation`.
        id: String,
        /// The commit of the Content's resolution. Re-resolving or reopening the item gives a new one, which makes
        /// tickets for the old one worthless.
        #[ts(type = "number")]
        generation: u64,
        /// The name the viewer shows.
        display_name: String,
    },
}

/// What the user asked to add; the intake turns each into an item.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(tag = "type")]
pub enum NewItem {
    /// A video file.
    Video {
        /// The file; a relative path is relative to the backend's working folder.
        path: String,
    },
}
