//! What a seat asks for over the Session socket: changes to the Doc, to its viewport and to the workspace file.

use crate::{
    bookmarks::BookmarkCommand,
    playback::LanePolicyWire,
    workspace::{HiddenTarget, NewItem},
};
use foundation::ItemId;
use serde::Deserialize;
use sessions::session::{ContentRef, Control, Overlay, ViewerId, Viewport};
use ts_rs::TS;

/// A change to the Doc's content; the backend runs the intake on every item before it applies any.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum DocCommand {
    /// Append items, all pending; with `open`, wait until every item has resolved or failed, then publish them in
    /// order and open a viewer for the first one that resolved.
    AddItems {
        /// What to add, in order.
        items: Vec<NewItem>,
        /// Whether to publish the results together and open a viewer for the first that resolved.
        open: bool,
    },
    /// Add the item that `POST /api/resolvers/{id}/resolve` resolved and answered with, open. The ticket is redeemed
    /// once; an id the backend does not hold is refused.
    AddResolved {
        /// The item the resolve answered with.
        #[ts(type = "string")]
        item: ItemId,
        /// The row's label; the resolver's name when absent or blank.
        #[serde(default)]
        label: Option<String>,
    },
    /// Request a viewer for an item.
    OpenViewer {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
    },
    /// Withdraw an item's viewer request.
    CloseViewer {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
    },
    /// Resolve an item again, such as a live stream whose host was unset. Ignored while it is resolving.
    Resolve {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
    },
    /// Remove an item from the workspace, closing its viewer.
    Remove {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
    },
    /// Name an item without resolving it again; a blank name, or the item's default, gives it the default name.
    RenameItem {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
        /// The name the user entered.
        label: String,
    },
    /// Replace the name and recipe of a video or live item with a new one of the same kind, keeping its id, place,
    /// bookmarks and reviewed marks. An edit that changes nothing does nothing, a new name alone resolves nothing, and
    /// a new recipe resolves again. A live stream's blank user and password keep the saved ones while it stays on the
    /// same camera, as `like` does for another stream.
    EditItem {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
        /// What the item becomes; the intake judges it as it judges an item to add.
        new: NewItem,
    },
    /// Replace a playlist item's name and recipe with the item that `POST /api/resolvers/{id}/resolve` resolved and
    /// answered with; the ticket is redeemed once.
    EditResolved {
        /// The item to edit.
        #[ts(type = "string")]
        item: ItemId,
        /// The item the resolve answered with.
        #[ts(type = "string")]
        ticket: ItemId,
        /// The item's name; the resolver's when absent or blank.
        #[serde(default)]
        label: Option<String>,
    },
    /// Hide or show an entry or lane.
    SetHidden {
        /// What to hide or show.
        target: HiddenTarget,
        /// Whether it is hidden afterwards.
        hidden: bool,
    },
    /// Set how a lane matches its file's records to frames, in the item's saved recipe.
    SetLaneMatching {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
        /// The lane's slot, which names it in every entry.
        slot: String,
        /// How the lane matches.
        policy: LanePolicyWire,
    },
    /// Rename the workspace; surrounding whitespace is dropped and a blank name or `Untitled` is Untitled; the file
    /// keeps its name. Ignored when `epoch` is not the workspace's, so a rename that was sent before another seat
    /// replaced the workspace does not name the replacement. Senders throttle it: the session command budget is 10/s
    /// (`src/session/reader.rs`).
    RenameWorkspace {
        /// The name the user entered.
        name: String,
        /// The `epoch` of the workspace the name was entered for.
        #[ts(type = "number")]
        epoch: u64,
    },
    /// Change the bookmarks or reviewed marks.
    Bookmark {
        /// The change.
        command: BookmarkCommand,
    },
}

/// What a seat asks for, settled by its own `Ack`. Changes to a seat's view travel the View lane instead.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum SessionCommand {
    /// Change the Doc.
    Content {
        /// The change.
        command: DocCommand,
    },
    /// Go back to following the presenter.
    Follow,
    /// Change one viewer's viewport in the view this seat shows; a Guest that follows starts exploring.
    SetViewport {
        /// The viewer.
        #[ts(type = "number")]
        viewer: ViewerId,
        /// The part of the video it shows; absent for the whole frame.
        #[serde(default)]
        #[ts(optional)]
        viewport: Option<Viewport>,
    },
    /// Turn an overlay on or off in the view this seat shows; a Guest that follows starts exploring.
    SetOverlay {
        /// The overlay.
        overlay: Overlay,
        /// Whether it is on afterwards.
        shown: bool,
    },
    /// Change sharing; the owner only.
    Control {
        /// The change.
        control: Control,
    },
    /// Ask for a ticket to open a stream; answered with `Ticket`.
    RequestTicket {
        /// The stream.
        key: ContentRef,
    },
    /// Open, save or close the workspace file; the owner only.
    Workspace {
        /// The change.
        command: WorkspaceCommand,
    },
}

/// What a seat asks of the workspace file. A path is absolute, on the host.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum WorkspaceCommand {
    /// An empty Untitled workspace.
    New,
    /// The workspace saved at `path`; a path inside the recovery folder restores that copy.
    Open {
        /// The file.
        path: String,
    },
    /// Write the workspace: to its own file, or with `path` to that file (Save As; ends with the workspace suffix).
    Save {
        /// The file to save as; the workspace's own file when absent.
        #[serde(default)]
        #[ts(optional)]
        path: Option<String>,
        /// Whether Save As may replace a file that is already there.
        #[serde(default)]
        #[ts(optional)]
        replace: Option<bool>,
    },
    /// Keep the changes restored from a recovery copy.
    Keep,
    /// Drop the changes restored from a recovery copy: back to the saved file, or an empty Untitled workspace.
    Discard,
    /// Write every bookmark to the CSV file at `path`, replacing it only when complete.
    ExportBookmarks {
        /// The file, absolute.
        path: String,
    },
}
