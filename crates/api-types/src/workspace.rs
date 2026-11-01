//! Wire types for the workspace routes and events.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::content::ContentInfo;

/// The workspace and what is known about each item: the body of `GET /api/workspace` and of every `Workspace` event.
#[derive(Serialize, Deserialize, TS)]
pub struct WorkspaceState {
    /// The user's name for the workspace; `null` is Untitled.
    pub name: Option<String>,
    /// Every item in the order it was added.
    pub items: Vec<ItemState>,
    /// The sidebar tree flattened: each item, its entries and their lanes, in tree order.
    pub rows: Vec<RowInfo>,
}

/// One row of the workspace tree.
#[derive(Serialize, Deserialize, TS)]
pub struct RowInfo {
    /// Names the row for as long as what it stands for exists.
    pub key: String,
    /// What the row stands for.
    pub kind: RowKindWire,
    /// What the user reads.
    pub label: String,
    /// Whether it can be opened.
    pub status: RowStatusInfo,
    /// Whether the user hid it.
    pub hidden: bool,
    /// The entry that opening the row opens, by the id the playback socket takes.
    pub entry: Option<String>,
}

/// What a row stands for.
#[derive(Clone, Copy, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum RowKindWire {
    /// A workspace item.
    Item,
    /// An entry of an item.
    Entry,
    /// A lane of an entry.
    Lane,
}

/// Whether what a row stands for can be opened.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum RowStatusInfo {
    /// Resolving has not finished.
    Pending,
    /// The item has no Content.
    Unavailable {
        /// Why, in words for the user.
        reason: String,
    },
    /// Ready to open.
    Ready,
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

/// What the user asked to add; the intake turns each into an item.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum NewItem {
    /// A video file, with overlay files drawn on it.
    Video {
        /// The file; a relative path is relative to the backend's working folder.
        path: String,
        /// The overlay files, each shown as its own lane.
        #[serde(default)]
        overlays: Vec<NewOverlayWire>,
    },
    /// A playlist from a resolver.
    Playlist {
        /// The resolver id.
        resolver: String,
        /// The settings the resolver's schema declares.
        #[ts(type = "Record<string, unknown>")]
        settings: serde_json::Map<String, serde_json::Value>,
        /// The row's label; the resolver's name when absent.
        #[serde(default)]
        label: Option<String>,
    },
}

/// An overlay file to add to a video.
#[derive(Serialize, Deserialize, TS)]
pub struct NewOverlayWire {
    /// The file.
    pub path: String,
    /// The decoder handler id; the only decoder for the file's extension when absent.
    #[serde(default)]
    pub handler: Option<String>,
}

/// The body of `POST /api/workspace/playlists`: resolves the playlist first and adds it only when it resolves.
#[derive(Serialize, Deserialize, TS)]
pub struct AddPlaylistRequest {
    /// The resolver id.
    pub resolver: String,
    /// The settings the resolver's schema declares.
    #[ts(type = "Record<string, unknown>")]
    pub settings: serde_json::Map<String, serde_json::Value>,
    /// The row's label; the resolver's name when absent.
    #[serde(default)]
    pub label: Option<String>,
}

/// An entry or lane to hide or show.
#[derive(Serialize, Deserialize, TS)]
pub struct HiddenTarget {
    /// The entry, by its content id.
    pub entry: String,
    /// The lane's label; the whole entry when absent.
    #[serde(default)]
    pub lane: Option<String>,
}

/// The body of `POST /api/workspace/hidden`.
#[derive(Serialize, Deserialize, TS)]
pub struct HiddenRequest {
    /// What to hide or show.
    pub target: HiddenTarget,
    /// Whether it is hidden afterwards.
    pub hidden: bool,
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
