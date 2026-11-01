//! The Session socket's wire protocol: what a client and the backend send over `/api/session`.
//!
//! Every time is microseconds (`server_us` is the backend's monotonic clock; see `TimeSync`). A message that changes
//! the Doc or a view carries the `seq` the client chose and is settled by its own `Ack` or, for views, by
//! `views_done`. The only `DocWire` constructor is `project` in the backend's `src/session/project.rs`, so what a
//! watcher may learn of the Doc is decided in one place.

use crate::{
    Access, View,
    workspace::{NewItem, WorkspaceState},
};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Longest element id or scope a cursor may carry, in characters.
pub const CURSOR_NAME_MAX: usize = 64;

/// One seat's two cursor choices; both are on by default.
#[derive(Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
pub struct CursorOptions {
    /// Receive other seats' cursors.
    pub show: bool,
    /// Broadcast this seat's cursor.
    pub share: bool,
}

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
    /// Request a viewer for an item.
    OpenViewer {
        /// The item.
        item: String,
    },
    /// Withdraw an item's viewer request.
    CloseViewer {
        /// The item.
        item: String,
    },
}

/// The owner's control of sharing.
#[derive(Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum ControlCommand {
    /// "Start sharing": watchers may join, with `access`.
    Share {
        /// What every watcher may do.
        access: Access,
    },
    /// Change what every watcher may do.
    SetAccess {
        /// What every watcher may do.
        access: Access,
    },
    /// Replace the join link; seats already in stay.
    RegenerateLink,
    /// Stop sharing: every watcher is removed. The Session and its owner continue.
    Stop,
}

/// A stream a seat asks a ticket for.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum StreamKey {
    /// The frames of a recorded video.
    Frames {
        /// The Content's id.
        content: String,
        /// The commit of the Content's resolution, as the Doc names it.
        #[ts(type = "number")]
        generation: u64,
    },
    /// The current frames of a live item.
    Live {
        /// The item.
        item: String,
        /// The commit of the item's Content.
        #[ts(type = "number")]
        generation: u64,
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
        control: ControlCommand,
    },
    /// Ask for a ticket to open a stream; answered with `Ticket`.
    RequestTicket {
        /// The stream.
        key: StreamKey,
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

/// Which seat a participant holds.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum SeatRole {
    /// Created the Session; the only one who changes what it shows.
    Owner,
    /// Anyone else.
    Watcher,
}

/// Whose view a seat shows.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum SeatMode {
    /// The presenter's.
    Following,
    /// The seat's own, limited to what the Doc has open.
    Exploring,
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

/// One seat of a Session, as every seat sees it.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SeatInfo {
    /// Names the seat in `Roster` and `Cursor` messages.
    pub id: u32,
    /// `Guest N`.
    pub name: String,
    /// An index into the UI's palette of participant colors.
    pub color: u8,
    /// Owner or watcher.
    pub role: SeatRole,
    /// Following or exploring.
    pub mode: SeatMode,
    /// False while the seat is within its grace period.
    pub present: bool,
    /// Whether the seat broadcasts its cursor.
    pub shares_cursor: bool,
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
}

/// The Doc as a watcher or the owner may see it. Built only by the backend's projection.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DocWire {
    /// The items, how they resolved, and which have a viewer open.
    pub workspace: WorkspaceState,
}

/// Why a command was refused. A code and nothing else: the client words it.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
pub enum RefusalCode {
    /// The seat is not in the Session, its connection was replaced, or the Session ended.
    SeatGone,
    /// The seat's standing does not allow it.
    NotAllowed,
    /// Sharing is off.
    NotSharing,
    /// Sharing is already on.
    AlreadySharing,
    /// The Content is not open, not resolved, or not at the generation asked for.
    ContentUnavailable,
    /// The seat or the Session holds as many streams as it may.
    TooManyStreams,
    /// The ticket is unknown, expired, used, or bound to something that no longer exists.
    BadTicket,
    /// Not decided yet.
    Unsupported,
    /// The link is no longer valid.
    Link,
    /// The Session already has as many seats as it may.
    Full,
    /// The command's own content was refused before it reached the Session, such as a path that names no file.
    Invalid,
}

/// How a command ended.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
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
        code: RefusalCode,
    },
}

/// Why a Session's socket was told it is over.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum EndedReason {
    /// The owner stopped sharing.
    Stopped,
    /// The owner was away longer than the grace period.
    OwnerGone,
    /// A newer connection took this seat; this tab stops for good.
    Replaced,
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
        participants: Vec<SeatInfo>,
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
        reason: EndedReason,
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
}
