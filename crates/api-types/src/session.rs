//! The session-sharing wire protocol: what a client and the backend send over `/api/share/socket`, and the session
//! list.

use crate::SidebarView;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Longest element id or scope a cursor may carry, in characters.
pub const CURSOR_NAME_MAX: usize = 64;

/// What the owner publishes and an exploring watcher edits for itself; the stand-in for the open viewers, zoom and
/// playback that will join it. It carries no settings, credentials or host paths.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ViewState {
    /// What the sidebar shows.
    pub sidebar_view: SidebarView,
    /// Whether the sidebar is open.
    pub sidebar_open: bool,
}

impl Default for ViewState {
    fn default() -> Self {
        Self {
            sidebar_view: SidebarView::default(),
            sidebar_open: true,
        }
    }
}

/// One seat's two cursor choices; both are on by default.
#[derive(Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
pub struct CursorOptions {
    /// Receive other seats' cursors.
    pub show: bool,
    /// Broadcast this seat's cursor.
    pub share: bool,
}

/// How a client asks to be seated; the first message of every socket.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum ShareAccess {
    /// Start a session and own it. Needs the Sessions grant.
    Create,
    /// Watch a session by id. Needs the Sessions grant.
    Watch {
        /// The session's id.
        session: String,
    },
    /// Watch the one session a join link opens.
    Join {
        /// The link's `<id>.<secret>`.
        link: String,
    },
    /// Take a seat back after a disconnect; the resume token alone is enough.
    Resume {
        /// The token from the last `Welcome`.
        token: String,
    },
}

/// Which seat a participant holds.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum SeatRole {
    /// Created the session; the only one who changes what it shows.
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
    /// The seat's own, limited to what the presenter has open.
    Exploring,
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

/// What the owner decides for the session.
#[derive(Serialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
pub struct ShareOptions {
    /// Watchers may explore on their own.
    pub explore: bool,
}

/// What a client sends over the share socket. Each role may send only its own messages; anything else closes the
/// socket with code 1008 and changes nothing.
#[derive(Deserialize, TS, Clone, Debug, PartialEq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum ShareClientMessage {
    /// The first message, within 5 seconds of connecting.
    Hello {
        /// How to be seated.
        access: ShareAccess,
        /// The server password; only a server that has one looks at it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        server_password: Option<String>,
        /// This seat's cursor choices.
        cursors: CursorOptions,
    },
    /// Owner: replace the presenter's view.
    Publish {
        /// The new view.
        state: ViewState,
    },
    /// Owner: change the session's options.
    SetOptions {
        /// Whether watchers may explore.
        explore: bool,
    },
    /// Owner: invalidate the join link and get a new one.
    RegenerateLink,
    /// Following watcher: start exploring, when the owner allows it.
    Explore,
    /// Exploring watcher: set the seat's own view.
    SetView {
        /// The view.
        state: ViewState,
        /// Counts this client's edits; the server echoes the latest accepted one as `viewSeq`.
        #[ts(type = "number")]
        seq: u64,
    },
    /// Exploring watcher: go back to the presenter's view.
    Follow,
    /// Move this seat's cursor, or `null` when it leaves the window.
    Cursor {
        /// The new position.
        at: Option<CursorPoint>,
    },
    /// Change this seat's cursor choices.
    CursorOptions(CursorOptions),
    /// Leave: an owner stops sharing, a watcher gives up its seat.
    Leave,
}

/// One seat of a session, as every seat sees it.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SeatInfo {
    /// Names the seat in snapshots and cursor messages.
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

/// Everything one seat sees of its session at one revision, including its own mode and view.
#[derive(Serialize, TS, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ShareSnapshot {
    /// Counts applied changes to the whole session.
    #[ts(type = "number")]
    pub revision: u64,
    /// The session's name.
    pub name: String,
    /// The owner's seat id.
    pub owner: u32,
    /// Whether the owner is connected; while not, the rest of this snapshot is the last one the owner left.
    pub owner_present: bool,
    /// What the owner allows.
    pub options: ShareOptions,
    /// Everyone in the session, oldest first.
    pub participants: Vec<SeatInfo>,
    /// The view the owner published.
    pub presenter: ViewState,
    /// Whether this seat follows or explores.
    pub mode: SeatMode,
    /// What this seat shows: its own view while exploring, the presenter's otherwise.
    pub view: ViewState,
    /// The latest `seq` of this seat's `setView` the session accepted; adopt `view` only when this is at least the
    /// last `seq` sent.
    #[ts(type = "number")]
    pub view_seq: u64,
}

/// Why a session's socket was told it is over.
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

/// What the backend sends over the share socket.
#[derive(Serialize, TS, Clone, Debug, PartialEq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum ShareServerMessage {
    /// Answers `Hello`.
    Welcome {
        /// The session's id.
        session: String,
        /// This seat's id.
        you: u32,
        /// Owner or watcher.
        role: SeatRole,
        /// Takes this seat back after a disconnect; each welcome brings a new one and kills the old.
        resume: String,
        /// The join link URL; only for owners and for holders of the Sessions grant.
        link: Option<String>,
    },
    /// This seat's view of the session; sent again only when something visible to this seat changed.
    Snapshot(ShareSnapshot),
    /// The owner's new join link after `regenerateLink`.
    Link {
        /// The link URL.
        url: String,
    },
    /// Another seat's cursor moved or left; a seat's own cursor is never sent back.
    Cursor {
        /// Whose cursor.
        participant: u32,
        /// Where it is now, or `null` when it left the window.
        at: Option<CursorPoint>,
    },
    /// Sent every 10 seconds; browsers cannot see protocol pings, so silence for 25 seconds means the link is down.
    Heartbeat,
    /// The socket closes after this.
    Ended {
        /// Why.
        reason: EndedReason,
    },
}

/// A line of `GET /api/share/sessions`.
#[derive(Serialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShareSummary {
    /// What to watch with.
    pub id: String,
    /// The session's name.
    pub name: String,
    /// Whether the owner is connected.
    pub owner_present: bool,
    /// Seats in the session, away ones included.
    pub participants: u32,
    /// The join link URL; `null` when this backend cannot be reached by other machines.
    pub link: Option<String>,
    /// Whether the caller owns the session.
    pub mine: bool,
}
