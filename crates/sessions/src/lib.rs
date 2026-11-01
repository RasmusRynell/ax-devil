//! Sessions: what one owner chooses to share, and the watchers who see it.
//!
//! Nothing here creates a session on its own: [`Sessions::create`] makes one, and its [`Owner`] is the only handle
//! that can change what it shows. [`Watcher`]s follow the owner's view or, when the owner allows it, explore their
//! own view of what the owner has open. A forbidden call returns a [`Violation`] and changes nothing.
//!
//! The session's whole internal state travels through one `watch` channel at one revision; each seat derives its
//! own [`Personal`] snapshot from it, so a seat's view can never arrive out of order with the shared state. The view
//! state `V` and the cursor position `C` are opaque, so this crate knows nothing of the wire or the UI.
//!
//! A seat whose handle is dropped is away for the grace period and can be taken back with [`Sessions::resume`].
//! Every seat has a lease that resuming bumps; a handle, a drop or a grace timer from an older lease does nothing.

mod feed;
mod registry;
mod seat;
mod secret;
mod view;

pub use feed::{CursorEvent, Cursors, Personal, Snapshots, Update};
pub use registry::Sessions;
pub use seat::{Connection, Owner, Role, Seat, SeatMessage, Watcher};
pub use secret::{JoinLink, Secret, SessionId};
pub use view::{View, ViewKey};

use std::fmt;

/// Seats one session holds, owner included.
pub const MAX_SEATS: usize = 32;

/// Names a participant, unique for the life of the process.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ParticipantId(pub u32);

/// Why a session, link or resume token was not accepted.
#[derive(Debug, PartialEq, Eq)]
pub enum SessionError {
    /// The text is not a session id, secret or join link.
    Malformed,
    /// No such session, or no seat holds that token; it ended or never existed.
    NotFound,
    /// The secret does not match the session.
    Rejected,
    /// The session already has [`MAX_SEATS`] seats.
    Full,
}

impl fmt::Display for SessionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Malformed => "that is not a session id or link",
            Self::NotFound => "that session no longer exists",
            Self::Rejected => "that link is no longer valid",
            Self::Full => "that session is full",
        })
    }
}

impl std::error::Error for SessionError {}

/// Why a seat's updates ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EndReason {
    /// The owner stopped sharing.
    Stopped,
    /// The owner was away longer than the grace period.
    OwnerGone,
    /// A newer connection resumed this seat.
    Replaced,
}

/// A call this seat's role or mode does not allow.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Violation {
    /// Only the other role may do this: a watcher cannot publish, an owner cannot explore.
    Role,
    /// The owner does not allow exploring (or just turned it off, so a message in flight lost the race).
    ExploreOff,
    /// Setting a view needs the seat to be exploring.
    NotExploring,
}

impl fmt::Display for Violation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Role => "your role cannot do that",
            Self::ExploreOff => "the owner does not let viewers explore",
            Self::NotExploring => "you are not exploring",
        })
    }
}

impl std::error::Error for Violation {}

/// What a seat is in the session.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// Created the session; the only one who changes what it shows.
    Owner,
    /// Anyone else.
    Watcher,
}

/// Whose view a seat shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// The presenter's. Owners are always following themselves.
    Following,
    /// The seat's own, limited to what the presenter has open.
    Exploring,
}

/// What the owner decides for the session.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Options {
    /// Watchers may explore on their own. Off by default.
    pub explore: bool,
}

/// One seat's cursor preferences.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CursorOptions {
    /// Receive others' cursors.
    pub show: bool,
    /// Broadcast this seat's cursor.
    pub share: bool,
}

impl Default for CursorOptions {
    fn default() -> Self {
        Self {
            show: true,
            share: true,
        }
    }
}

/// One seat as every seat sees it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Participant {
    /// Identifies the participant in snapshots and cursor events.
    pub id: ParticipantId,
    /// `Guest N`, the lowest N no seat in the session uses.
    pub name: String,
    /// The lowest color index no seat in the session uses.
    pub color: u8,
    /// Owner or watcher.
    pub kind: Kind,
    /// Following or exploring.
    pub mode: Mode,
    /// False while the seat is within its grace period.
    pub present: bool,
    /// Whether the seat broadcasts its cursor.
    pub shares_cursor: bool,
}

/// A line of the session list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Summary {
    /// Names the session.
    pub id: SessionId,
    /// The session's name: its owner's.
    pub name: String,
    /// Whether the owner is connected.
    pub owner_present: bool,
    /// Seats in the session, away ones included.
    pub participants: usize,
    /// The current join link. Hand it only to callers allowed to see every session.
    pub link: JoinLink,
}
