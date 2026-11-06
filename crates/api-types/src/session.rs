//! The Session socket's wire protocol: what a client and the backend send over `/api/session`.
//!
//! Every time is microseconds (`server_us` is the backend's monotonic clock; see `TimeSync`). A message that changes
//! the Doc or a view carries the `seq` the client chose and is settled by its own `Ack` or, for views, by
//! `views_done`. The only `DocWire` constructor is `project` in the backend's `src/session/project.rs`, so what a
//! watcher may learn of the Doc is decided in one place.

use crate::{
    ErrorWire,
    bookmarks::BookmarkCommand,
    playback::LanePolicyWire,
    workspace::{HiddenTarget, NewItem, WorkspaceState},
};
use foundation::ItemId;
use serde::{Deserialize, Serialize};
use sessions::{
    CursorOptions, EndReason, SeatMode,
    session::{Access, Can, ContentRef, Control, Refusal, RosterEntry, View},
};
use ts_rs::TS;

/// Longest element id or scope a cursor may carry, in characters.
pub const CURSOR_NAME_MAX: usize = 64;

/// How a client asks to be admitted; part of the first message of every socket.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum SessionAuth {
    /// Your own Session, which exists from this connection on.
    Own {
        /// The server password; only a server that has one looks at it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        server_password: Option<String>,
    },
    /// Watch a shared Session by id. Needs the Sessions grant.
    Watch {
        /// The Session's id.
        session: String,
        /// The server password; only a server that has one looks at it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        server_password: Option<String>,
    },
    /// Watch the one Session a join link opens.
    Join {
        /// The link's `<id>.<secret>`.
        link: String,
    },
}

/// A change to the Doc's content; the backend runs the intake on every item before it applies any.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum DocCommand {
    /// Append items, all pending; with `open`, open a viewer for each once it resolves.
    AddItems {
        /// What to add, in order.
        items: Vec<NewItem>,
        /// Whether a viewer should open for each item once it resolves.
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

/// What a client sends over the Session socket. Anything a seat's standing does not allow is answered with a refused
/// `Ack` (commands) or settled without effect (views); only a malformed or oversized message, or more commands than
/// the command budget, closes the socket.
#[derive(Deserialize, TS, Clone, Debug, PartialEq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum SessionClientMessage {
    /// The first message, within 5 seconds of connecting.
    Hello {
        /// How to be admitted. Not looked at when `resume` is set: the token alone is the credential.
        auth: SessionAuth,
        /// The token from the last `Welcome`: takes that seat back instead of creating or joining.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        resume: Option<String>,
        /// This seat's cursor choices.
        cursors: CursorOptions,
    },
    /// Set this seat's view: the presenter's for the owner, the seat's own for an exploring or editing seat.
    View {
        /// Counts this client's views; `views_done` reports the highest one finished.
        #[ts(type = "number")]
        seq: u64,
        /// The view.
        view: View,
    },
    /// Ask for a change.
    Command {
        /// Counts this client's commands; the `Ack` echoes it.
        #[ts(type = "number")]
        seq: u64,
        /// The change.
        cmd: SessionCommand,
    },
    /// Move this seat's cursor, or `null` when it leaves the window.
    Cursor {
        /// The new position.
        at: Option<CursorPoint>,
    },
    /// Change this seat's cursor choices.
    CursorOptions(CursorOptions),
    /// Measure the clock: echoed with the backend's time.
    TimeSync {
        /// Names the request.
        #[ts(type = "number")]
        id: u64,
        /// The client's clock when it sent this.
        #[ts(type = "number")]
        client_us: i64,
    },
    /// Leave at once instead of keeping the seat for the grace period. The owner leaving ends the Session.
    Leave,
}

/// What this seat is allowed to do, in the rows of the role table.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum SeatAccess {
    /// The Session's owner.
    Owner,
    /// A watcher that follows the presenter.
    Watch,
    /// A watcher that may explore a view of its own.
    Explore,
    /// A watcher that may explore and change the Doc's content.
    Edit,
}

impl From<Access> for SeatAccess {
    fn from(access: Access) -> Self {
        match access {
            Access::Watch => Self::Watch,
            Access::Explore => Self::Explore,
            Access::Edit => Self::Edit,
        }
    }
}

/// Sharing, while it is on.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SharingInfo {
    /// What every watcher may do.
    pub access: Access,
    /// The join link URL; the owner's seat only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub link: Option<String>,
    /// Why there is no link, when other machines cannot reach this backend; the owner's seat only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub link_problem: Option<String>,
}

/// The Doc as a watcher or the owner may see it. Built only by the backend's projection.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DocWire {
    /// The items, how they resolved, and which have a viewer open.
    pub workspace: WorkspaceState,
}

/// How a command ended.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum AckOutcome {
    /// Done.
    Ok,
    /// Refused; nothing changed.
    Refused {
        /// Why.
        code: Refusal,
    },
    /// Could not be done; nothing changed.
    Failed {
        /// Why.
        error: ErrorWire,
    },
}

/// What the backend sends over the Session socket.
#[derive(Serialize, TS)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum SessionServerMessage {
    /// Answers `Hello`; every part follows.
    Welcome {
        /// The Session's id, which `GET /api/sessions` lists it by while it is shared.
        session: String,
        /// This seat's id.
        seat: u32,
        /// Takes this seat back after a disconnect; each welcome brings a new one and kills the old.
        resume: String,
        /// The highest View `seq` of this seat that is finished; send later views above it.
        #[ts(type = "number")]
        views_done: u64,
        /// The backend's clock when it sent this.
        #[ts(type = "number")]
        server_us: i64,
    },
    /// The Doc at one revision. Always sent before a `Seat` whose `doc_rev` is newer.
    Doc {
        /// Counts changes to the Doc.
        #[ts(type = "number")]
        doc_rev: u64,
        /// The Doc as this seat may see it.
        doc: DocWire,
    },
    /// This seat's part of the Session at one revision.
    Seat {
        /// Counts changes any seat can see.
        #[ts(type = "number")]
        rev: u64,
        /// The Doc revision `view` belongs to; the Doc for it has been sent.
        #[ts(type = "number")]
        doc_rev: u64,
        /// The presenter's view revision.
        #[ts(type = "number")]
        presenter_rev: u64,
        /// What this seat may do.
        access: SeatAccess,
        /// What this seat may do, from the same role table the backend enforces.
        can: Can,
        /// Whether this seat follows or explores.
        mode: SeatMode,
        /// Sharing, while it is on.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        sharing: Option<SharingInfo>,
        /// What this seat shows: its own view while exploring, the presenter's otherwise.
        view: View,
        /// The highest View `seq` of this seat that is finished: accepted, refused or overwritten.
        #[ts(type = "number")]
        views_done: u64,
    },
    /// Everyone in the Session, oldest first; sent again when it changes.
    Roster {
        /// The seats.
        participants: Vec<RosterEntry>,
    },
    /// Settles a command.
    Ack {
        /// The command's `seq`.
        #[ts(type = "number")]
        seq: u64,
        /// How it ended.
        outcome: AckOutcome,
    },
    /// Answers a `RequestTicket`. Present it once, within `expires_ms`, as the first message of a playback socket.
    Ticket {
        /// The `RequestTicket` command's `seq`.
        #[ts(type = "number")]
        seq: u64,
        /// The bearer ticket: never put it in a URL or a log.
        ticket: String,
        /// How long it can be redeemed, in milliseconds.
        #[ts(type = "number")]
        expires_ms: u64,
    },
    /// Answers `TimeSync`.
    TimeSyncReply {
        /// The request's `id`.
        #[ts(type = "number")]
        id: u64,
        /// The request's `client_us`.
        #[ts(type = "number")]
        client_us: i64,
        /// The backend's clock when it answered.
        #[ts(type = "number")]
        server_us: i64,
    },
    /// Sent every 10 seconds; browsers cannot see protocol pings, so silence for 25 seconds means the link is down.
    Heartbeat {
        /// The backend's clock when it sent this.
        #[ts(type = "number")]
        server_us: i64,
    },
    /// Another seat's cursor moved or left; a seat's own cursor is never sent back.
    Cursor {
        /// Whose cursor.
        participant: u32,
        /// Where it is now, or `null` when it left the window.
        at: Option<CursorPoint>,
    },
    /// The socket closes after this.
    Ended {
        /// Why.
        reason: EndReason,
    },
}

/// A line of `GET /api/sessions`: a Session that is shared.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SessionSummary {
    /// What to watch with.
    pub id: String,
    /// Whether the owner is connected.
    pub owner_present: bool,
    /// Seats in the Session, away ones included.
    pub participants: u32,
    /// The join link URL; absent when this backend cannot be reached by other machines.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub link: Option<String>,
    /// Why there is no link, when other machines cannot reach this backend.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub link_problem: Option<String>,
}
