//! Sessions: what one window shows, held by the backend from the window's first connect, and the watchers it is
//! shared with.
//!
//! [`session::Service`] holds every session of one backend. A [`session::Handle`] is one seat's connection: the
//! owner's, which exists from the first connect, or a watcher's once the owner turns sharing on. A seat whose handle
//! is dropped is away for the grace period and can be taken back with [`session::Service::resume`]. Every seat has a
//! lease that resuming bumps; a handle, a drop or a grace timer from an older lease does nothing.
//!
//! The session's state and the rules that change it are in [`session`]; this crate knows nothing of the wire or the
//! UI. The cursor position type `C` is opaque.

mod common;
mod cursors;
mod secret;
pub mod session;

pub use cursors::{CursorEvent, Cursors};
pub use secret::{JoinLink, OwnerId, Secret, SessionId};

use std::fmt;

/// Seats one session holds, owner included.
pub const MAX_SEATS: usize = 32;

/// Sessions one server holds, those in their grace period included.
pub const MAX_SESSIONS: usize = 64;

/// Names a seat in its session. Never reused within the session.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
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
    /// The session already has [`MAX_SEATS`] seats, or the server already has [`MAX_SESSIONS`] sessions.
    Full,
}

impl fmt::Display for SessionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Malformed => "that is not a session id or link",
            Self::NotFound => "that session no longer exists",
            Self::Rejected => "that link is no longer valid",
            Self::Full => "that session or server is full",
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
