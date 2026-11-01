//! The sessions of one backend, the settings of their sockets, and the session list.

use crate::{
    AppState,
    access::{Principal, SessionsAccess},
    share_wire::{ShareSessions, Shown},
};
use ::sessions::{CursorOptions, Owner};
use api_types::{CursorPoint, ShareSummary};
use axum::{Json, extract::State};
use std::{
    sync::{Mutex, PoisonError},
    time::Duration,
};

/// The times and caps of the share sockets; tests shorten the times.
#[derive(Clone, Copy)]
pub struct ShareConfig {
    /// How long a new socket has to send `Hello`.
    pub hello_timeout: Duration,
    /// How often the server pings.
    pub ping_interval: Duration,
    /// How long a seat may say nothing before it counts as disconnected.
    pub silence_timeout: Duration,
    /// How long one send may take before the seat counts as disconnected.
    pub send_deadline: Duration,
    /// How often the server sends `Heartbeat`, for browsers that cannot see pings.
    pub heartbeat_interval: Duration,
    /// How long a disconnected seat keeps its place.
    pub grace: Duration,
    /// Sessions one server holds.
    pub max_sessions: usize,
    /// How often the desktop's listener for other machines looks for sessions.
    pub listener_poll: Duration,
}

impl Default for ShareConfig {
    fn default() -> Self {
        Self {
            hello_timeout: Duration::from_secs(5),
            ping_interval: Duration::from_secs(10),
            silence_timeout: Duration::from_secs(25),
            send_deadline: Duration::from_secs(10),
            heartbeat_interval: Duration::from_secs(10),
            grace: Duration::from_secs(30),
            max_sessions: 64,
            listener_poll: Duration::from_secs(1),
        }
    }
}

/// The sessions of one backend and the settings of their sockets.
pub struct Share {
    pub(crate) sessions: ShareSessions,
    pub(crate) config: ShareConfig,
    /// Makes the count of sessions and the creation of one a single step.
    creating: Mutex<()>,
}

impl Share {
    pub(crate) fn new(config: ShareConfig) -> Self {
        Self {
            sessions: ShareSessions::new(config.grace),
            config,
            creating: Mutex::new(()),
        }
    }

    /// Whether no session exists.
    pub(crate) fn is_empty(&self) -> bool {
        self.sessions.list().is_empty()
    }

    pub(crate) fn create(&self, cursors: CursorOptions) -> Option<Owner<Shown, CursorPoint>> {
        let _creating = self.creating.lock().unwrap_or_else(PoisonError::into_inner);
        (self.sessions.list().len() < self.config.max_sessions)
            .then(|| self.sessions.create(cursors))
    }
}

pub(crate) async fn list(
    SessionsAccess(principal): SessionsAccess,
    State(state): State<AppState>,
) -> Json<Vec<ShareSummary>> {
    Json(
        state
            .share
            .sessions
            .list()
            .into_iter()
            .map(|summary| ShareSummary {
                link: state.hosting.join_url(&summary.link),
                id: summary.id.to_string(),
                name: summary.name,
                owner_present: summary.owner_present,
                participants: u32::try_from(summary.participants).unwrap_or(u32::MAX),
                // Only the person at this machine can have created a session here; a server cannot tell its
                // password holders apart.
                mine: principal == Principal::Local,
            })
            .collect(),
    )
}
