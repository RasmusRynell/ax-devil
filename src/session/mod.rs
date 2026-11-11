//! The adapter between the Session service and its socket.
//!
//! [`socket`] admits a connection and runs its two halves: `reader` applies what the client sends on three lanes,
//! `writer` sends the Doc, the seat's part, the roster and the cursors as they change. [`project`] decides what each
//! audience learns of the Doc; `wire` converts between the wire types and the sessions crate; `files` runs the
//! workspace's own file commands and keeps the recovery copy of unsaved work.

mod exit;
mod files;
#[cfg(all(test, unix))]
mod frontend_fixtures;
mod launch;
mod list;
pub mod live;
pub mod project;
mod reader;
mod saved;
mod socket;
mod topics;
mod wire;
mod writer;

pub use files::{WorkspaceFiles, WorkspaceStorage};
pub use launch::{HandOff, Launch, LaunchQueue};
pub(crate) use list::list;
pub(crate) use socket::socket;

use api_types::CursorPoint;
use std::time::Duration;

/// Every session of one backend, whichever listener its socket arrives on. Cursor positions are wire values.
pub type Sessions = ::sessions::session::Service<CursorPoint>;

/// How long a new socket has to send its first message: `Hello` on the Session socket, `Open` on the playback socket.
pub(crate) const FIRST_MESSAGE_TIMEOUT: Duration = Duration::from_secs(5);
/// How long a seat whose connection dropped keeps its place.
pub(crate) const GRACE: Duration = Duration::from_secs(30);
