//! The workspace as the Session socket carries it: the Doc's wire form and the items a client asks to add.

use crate::ErrorWire;
use serde::{Deserialize, Serialize};
use ts_rs::TS;
use workspace::ContentId;

use crate::{
    bookmarks::BookmarkGroupInfo, content::ContentInfo, live::NewLiveWire, playback::WarningInfo,
};

/// The workspace and what is known about each item, as one audience may see it.
#[derive(Serialize, Deserialize, TS)]
pub struct WorkspaceState {
    /// The user's name for the workspace; `null` is Untitled.
    pub name: Option<String>,
    /// Whether the workspace differs from what was last opened or saved.
    pub modified: bool,
    /// Whether changes restored from a recovery copy await Keep or Discard.
    pub recovered: bool,
    /// Counts replacements of the workspace; every viewer closes when it changes.
    #[ts(type = "number")]
    pub epoch: u64,
    /// The workspace file on the host. Only for an audience with host details.
    pub file: Option<String>,
    /// Every item in the order it was added.
    pub items: Vec<ItemState>,
    /// The sidebar tree flattened: each video or unresolved item, each playlist, their entries and lanes, in tree order. Lists only the items
    /// `items` lists.
    pub rows: Vec<RowInfo>,
    /// The bookmarks and reviewed marks of the items `items` lists, a group for each video or entry with any.
    pub bookmarks: Vec<BookmarkGroupInfo>,
}

/// A workspace file opened or saved lately.
#[derive(Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct RecentWorkspace {
    /// The file to open, absolute: a workspace file, or the recovery copy of unsaved changes to one.
    pub path: String,
    /// The containing folder for display, with the home shortened only for an audience allowed host details.
    pub folder: String,
    /// The workspace file name this row is for, with its suffix: the saved file's for a recovery copy of it.
    pub name: String,
    /// When it was last used, in Unix milliseconds.
    #[ts(type = "number")]
    pub used_ms: u64,
}

/// Pending changes contain only identities already carried by the Content projection.
#[derive(Serialize, Deserialize, TS)]
pub struct SourceChangesInfo {
    /// A pending whole-item change, absent for changes only to resolver inputs.
    pub item: Option<u32>,
    /// Pending changes by active entry identity; no file paths cross this boundary.
    pub entries: std::collections::BTreeMap<String, u32>,
}

/// One row of the workspace tree.
#[derive(Serialize, Deserialize, TS)]
pub struct RowInfo {
    /// Names the row for as long as what it stands for exists.
    pub key: String,
    /// The item the row belongs to, which a viewer is opened on.
    pub item: String,
    /// What the row stands for.
    pub kind: RowKindWire,
    /// What the user reads.
    pub label: String,
    /// What tells it from a sibling with the same label: where it comes from, or its number; empty for most rows. The
    /// sidebar shows it under the label, and the search matches it.
    pub detail: String,
    /// The file or camera it is read from, which the Offline view's Location order sorts by; none for a playlist, a row
    /// not resolved, and an audience without host details.
    pub location: Option<String>,
    /// Whether it can be opened.
    pub status: RowStatusInfo,
    /// Whether the user hid it.
    pub hidden: bool,
    /// The entry that opening the row opens, by the id the playback socket takes.
    pub entry: Option<String>,
    /// What resolving left out of an item's Content; empty for entries and lanes.
    pub warnings: Vec<WarningInfo>,
}

/// What a row stands for.
#[derive(Clone, Copy, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum RowKindWire {
    /// A video item, or an item with no Content yet.
    Item,
    /// One playlist of a playlist item.
    Playlist,
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
    /// The item has no Content, and why.
    Unavailable(ErrorWire),
    /// Ready to open.
    Ready,
}

/// One workspace item and how far it has resolved.
#[derive(Serialize, Deserialize, TS)]
pub struct ItemState {
    /// Names the item.
    pub id: String,
    /// The name the item shows: what the user named it, or its default.
    pub label: String,
    /// What the item is, which a sidebar view of one kind lists before it has resolved.
    pub kind: ItemKind,
    /// Whether a viewer should open for the item once it resolves. Stays set; the frontend remembers which
    /// viewers it has opened.
    pub open: bool,
    /// Whether the item has Content yet.
    pub resolution: ResolutionState,
    /// Pending source revisions: a playlist viewer offers only its shown entry, as resolver inputs do not change
    /// the overlays on screen. Single-file viewers use the item revision.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub changes: Option<SourceChangesInfo>,
    /// Why the last reload of the open viewer failed, until the next. The viewer keeps its old Content.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub reload_failure: Option<ReloadFailure>,
    /// What the item was made from, for an audience with host details: what Edit starts from and Information lists.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub recipe: Option<ItemRecipe>,
    /// For a live stream, for an audience with host details: what its hosts read as under the Session's right to
    /// expand `$NAME` references, the one rule resolving and labels follow.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub shown: Option<LiveShown>,
}

/// The hosts of a live stream as the Session reads them: a `$NAME` reference is its value (empty when the variable
/// is not set) where the Session may expand references, and the text as typed where it may not.
#[derive(Serialize, Deserialize, TS)]
pub struct LiveShown {
    /// The camera host.
    pub host: String,
    /// The MQTT broker host; `null` for another overlay.
    pub broker_host: Option<String>,
}

/// A reload that failed.
#[derive(Serialize, Deserialize, TS)]
pub struct ReloadFailure {
    /// Which attempt failed, so a dismissed failure does not hide the next one.
    #[ts(type = "number")]
    pub attempt: u64,
    /// Why.
    pub error: ErrorWire,
}

/// What an item was made from, as stored: a `$NAME` reference stays one and a literal password is left out. The name
/// is the one the user gave, absent for the default.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "type")]
pub enum ItemRecipe {
    /// A video file with its overlay files.
    Video {
        /// The file.
        path: String,
        /// The user's name for the item.
        label: Option<String>,
        /// The overlay files, each a lane.
        overlays: Vec<NewOverlayWire>,
    },
    /// A playlist from a resolver.
    Playlist {
        /// The resolver id.
        resolver: String,
        /// The user's name for the item.
        label: Option<String>,
        /// The settings the resolver's schema declares.
        #[ts(type = "Record<string, unknown>")]
        settings: serde_json::Map<String, serde_json::Value>,
    },
    /// A camera's live stream.
    Live(Box<NewLiveWire>),
}

/// What an item is, from its recipe.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum ItemKind {
    /// A video file with overlay files.
    Video,
    /// A playlist from a resolver.
    Playlist,
    /// A camera's live stream.
    Live,
    /// An item this build cannot read, kept as it was.
    Unreadable,
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

/// What the user asked to add; the intake turns each into an item.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(tag = "type")]
pub enum NewItem {
    /// A video file, with overlay files drawn on it.
    Video {
        /// The file; a relative path is relative to the backend's working folder.
        path: String,
        /// The item's name; the file name when absent or blank.
        #[serde(default)]
        #[ts(optional)]
        label: Option<String>,
        /// The overlay files, each shown as its own lane.
        #[serde(default)]
        overlays: Vec<NewOverlayWire>,
    },
    /// A camera's live stream. Adding it connects to nothing: the stream starts when its viewer opens.
    Live(Box<NewLiveWire>),
}

/// An overlay file to add to a video.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct NewOverlayWire {
    /// The file.
    pub path: String,
    /// The decoder handler id; the only decoder for the file's extension when absent.
    #[serde(default)]
    pub handler: Option<String>,
    /// The lane's name; the file name when absent, numbered ` (2)`, ` (3)` when the video already shows the file.
    #[serde(default)]
    pub label: Option<String>,
    /// The options the decoder's form declares; checked by the intake.
    #[serde(default)]
    #[ts(type = "Record<string, unknown>")]
    pub options: serde_json::Map<String, serde_json::Value>,
}

/// The body of `POST /api/files/check`: the files a form is about to add, as typed.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct CheckFilesRequest {
    /// The paths; a relative one is relative to the backend's working folder.
    pub paths: Vec<String>,
}

/// What the backend host makes of one path.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct FileCheck {
    /// The path as asked.
    pub path: String,
    /// Why it cannot be read as a file; absent when it can.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub problem: Option<String>,
}

/// What `GET /api/files/folder` answers: one folder on the backend's machine, and where a listing can start.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct FolderListing {
    /// The folder's canonical path; absent when no folder was asked for.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub path: Option<String>,
    /// The folder that holds it; absent at a root and when no folder was asked for.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub parent: Option<String>,
    /// The visible entries it holds, folders first, each group by name ignoring case.
    pub entries: Vec<FolderEntry>,
    /// Where to start: the home folder, then the filesystem roots. Empty when a folder was asked for.
    pub places: Vec<FolderPlace>,
}

/// What `GET /api/files/exists` answers: whether anything, hidden or not, is at a path on the backend's machine.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct PathExists {
    /// Whether a file or folder is there.
    pub exists: bool,
}

/// One entry of a listed folder.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct FolderEntry {
    /// The file or folder name.
    pub name: String,
    /// Whether it is a folder; otherwise a file.
    pub folder: bool,
}

/// A folder to start a listing from.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct FolderPlace {
    /// What to call it: `Home`, or the root's own path.
    pub name: String,
    /// The folder's absolute path.
    pub path: String,
}

/// An entry or lane to hide or show.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
pub struct HiddenTarget {
    /// The entry, by its content id.
    #[ts(type = "string")]
    pub entry: ContentId,
    /// The lane's label; the whole entry when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub lane: Option<String>,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<WorkspaceState>();
    types.visit::<RecentWorkspace>();
    types.visit::<SourceChangesInfo>();
    types.visit::<RowInfo>();
    types.visit::<RowKindWire>();
    types.visit::<RowStatusInfo>();
    types.visit::<ItemState>();
    types.visit::<LiveShown>();
    types.visit::<ReloadFailure>();
    types.visit::<ItemRecipe>();
    types.visit::<ItemKind>();
    types.visit::<ResolutionState>();
    types.visit::<NewItem>();
    types.visit::<NewOverlayWire>();
    types.visit::<CheckFilesRequest>();
    types.visit::<FileCheck>();
    types.visit::<FolderListing>();
    types.visit::<PathExists>();
    types.visit::<FolderEntry>();
    types.visit::<FolderPlace>();
    types.visit::<HiddenTarget>();
}
