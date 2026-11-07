//! Sessions: what one window shows, held by the backend from the window's first connect, and the watchers it is
//! shared with.
//!
//! [`session::Service`] holds every session of one backend. A [`session::Handle`] is one seat's connection: the
//! owner's, which exists from the first connect, or a watcher's once the owner turns sharing on. A seat whose handle
//! is dropped is away for the grace period and can be taken back with [`session::Service::resume`]. Every seat has a
//! lease that resuming bumps; a handle, a drop or a grace timer from an older lease does nothing.
//!
//! The session's state and the rules that change it are in [`session`]. The plain data that crosses to a client, such
//! as a view or a seat's role, is defined here once and carries its own wire form; the messages are not this crate's,
//! and nor is the UI. The cursor position type `C` is opaque.

mod common;
mod cursors;
mod secret;
pub mod session;

pub use cursors::{CursorEvent, Cursors};
pub use secret::{JoinLink, OwnerId, Secret, SessionId};

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Seats one session holds, owner included.
pub const MAX_SEATS: usize = 32;

/// Sessions one server holds, those in their grace period included.
pub const MAX_SESSIONS: usize = 64;

/// Names a seat in its session. Never reused within the session.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct ParticipantId(pub u32);

/// Why a Session's socket was told it is over.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "EndedReason")]
pub enum EndReason {
    /// The owner stopped sharing.
    Stopped,
    /// The owner was away longer than the grace period.
    OwnerGone,
    /// A newer connection took this seat; this tab stops for good.
    Replaced,
}

/// Which seat a participant holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum SeatRole {
    /// Created the Session; the only one who changes what it shows.
    Owner,
    /// Anyone else.
    Watcher,
}

/// Whose view a seat shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum SeatMode {
    /// The presenter's. Owners are always following themselves.
    Following,
    /// The seat's own, limited to what the Doc has open.
    Exploring,
}

/// One seat's two cursor choices; both are on by default.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, TS)]
pub struct CursorOptions {
    /// Receive other seats' cursors.
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
