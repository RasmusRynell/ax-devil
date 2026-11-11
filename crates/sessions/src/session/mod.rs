//! The Session: one window's shared state, held by the backend from the window's first connect.
//!
//! A [`SessionState`] is an immutable value. Every change is [`transition`], a pure function that returns the next
//! state and the [`Effects`] to run once the session's lock is released; [`Service`] runs it under that lock and
//! publishes the result. [`authorize`] is the one rule for what a seat may do: commands and streams alike. Three
//! generations make old authority dead for good: the [`SharingGen`] of a "Start sharing", the
//! [`ContentGen`](workspace_store::ContentGen) of a resolved Content, and the seat's [`Lease`].

mod authorize;
mod commands;
mod handle;
mod jobs;
mod layout;
mod listing;
mod roster;
mod runner;
mod service;
mod sources;
mod state;
mod streams;
mod transition;
mod views;

pub use authorize::{Can, ContentRef, Refusal, Use, authorize, authorize_stream, can};
pub use commands::{Command, Control, Scope};
pub use handle::{Change, Feed, Handle, Reply, StreamLease};
pub use jobs::{Completion, JobOwner, Spawn, Work};
pub use layout::{Axis, Layout, MAX_PANES, ViewerId};
pub use listing::Listing;
pub use roster::RosterEntry;
pub use runner::{Completer, OnRuntime, Runner, Watcher};
pub use service::{HeldItem, Service};
pub use sources::poll as poll_sources;
pub use state::{Doc, Seat, SeatKind, SessionState, Sharing};
pub use streams::{TICKET_TTL, Ticket};
pub use transition::{Effects, Fresh, Input, Outcome, transition};
pub use views::{Sidebar, SidebarView, View, ViewLane, ViewerView, restrict};

/// Names a seat in its session. Never reused within the session.
pub use crate::ParticipantId as SeatId;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Counts the replacements of a seat's connection. A handle, ticket or grant of an older lease does nothing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Lease(pub u64);

/// Names one "Start sharing". A new one for every `Share`, never reused: seats, tickets and grants of an older one
/// match nothing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct SharingGen(pub u64);

/// Names one authorized stream. Never reissued within the session, so a removed grant stays removed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct GrantId(pub u64);

/// Names one job. Exists only through a spawn, and is never reused within the session.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct JobId(pub u64);

/// What Guests may do while sharing is on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum Access {
    /// Follow the presenter.
    Watch,
    /// Follow, or explore a view of their own.
    Explore,
}

/// Visits the types this module sends to the frontend, and its modules' types.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<Access>();
    authorize::visit_api_types(types);
    commands::visit_api_types(types);
    layout::visit_api_types(types);
    roster::visit_api_types(types);
    views::visit_api_types(types);
}
