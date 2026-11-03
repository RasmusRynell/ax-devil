//! The reader half of a Session socket: what the client sends, on three lanes with their own budgets.
//!
//! - Cursor (and time sync): 60 a second, over budget dropped.
//! - View: only the latest one waits and at most 60 a second are applied; an overwritten one counts as done. Never
//!   closes the socket. A command sent after a waiting view applies that view first, so order is kept.
//! - Command: 10 a second, over budget closes the socket with 4429.
//!
//! Anything a seat's standing does not allow is answered with a refused `Ack` or settled without effect; only a
//! malformed or oversized message, or too many commands, ends the connection.

use super::{
    exit::{Exit, INVALID_DATA, NOT_ALLOWED, TOO_BIG, TOO_MANY_COMMANDS},
    wire::{self, Bucket, Request},
};
use crate::AppState;
use api_types::{AckOutcome, CursorPoint, SessionClientMessage, SessionServerMessage};
use axum::extract::ws::{Message, WebSocket};
use futures_util::{StreamExt, stream::SplitStream};
use sessions::session::{
    Command, Control, Handle, Reply, Scope, TICKET_TTL, Use, View, ViewLane, authorize,
};
use std::{convert::Infallible, time::Duration};
use tokio::{
    sync::mpsc,
    time::{Instant, sleep, timeout_at},
};

/// How long a seat may say nothing before it counts as disconnected. The client's heartbeats and pings keep it alive.
const SILENCE_TIMEOUT: Duration = Duration::from_secs(25);
const CURSORS_PER_SECOND: u32 = 60;
const COMMANDS_PER_SECOND: u32 = 10;

/// Applies what the seat sends until it leaves, breaks a rule or goes quiet. Answers go to `replies`.
pub(super) async fn run(
    state: &AppState,
    stream: &mut SplitStream<WebSocket>,
    handle: &Handle<CursorPoint>,
    replies: mpsc::Sender<SessionServerMessage>,
) -> Result<Infallible, Exit> {
    let mut cursors = Bucket::new(CURSORS_PER_SECOND);
    let mut commands = Bucket::new(COMMANDS_PER_SECOND);
    let mut views = ViewLane::default();
    let mut heard = Instant::now();
    loop {
        let wait = views.wait(Instant::now());
        let item = tokio::select! {
            item = timeout_at(heard + SILENCE_TIMEOUT, stream.next()) => item.map_err(|_| Exit::Gone)?,
            () = sleep(wait.unwrap_or_default()), if wait.is_some() => {
                if let Some(taken) = views.take(Instant::now()) {
                    apply_view(handle, taken);
                }
                continue;
            }
        };
        heard = Instant::now();
        let text = match item {
            None | Some(Ok(Message::Close(_))) => return Err(Exit::Gone),
            Some(Err(_)) => return Err(Exit::close(TOO_BIG, "invalid or oversized frame")),
            Some(Ok(Message::Text(text))) => text,
            Some(Ok(Message::Binary(_))) => return Err(Exit::close(INVALID_DATA, "expected text")),
            // Pings are answered by the socket itself; both only show the peer is alive.
            Some(Ok(Message::Ping(_) | Message::Pong(_))) => continue,
        };
        let message = serde_json::from_str::<SessionClientMessage>(&text)
            .map_err(|error| Exit::Close(INVALID_DATA, error.to_string()))?;
        match message {
            SessionClientMessage::Hello { .. } => {
                return Err(Exit::close(NOT_ALLOWED, "hello was already sent"));
            }
            SessionClientMessage::Leave => return Err(Exit::Left),
            SessionClientMessage::Cursor { at } => {
                if cursors.take() {
                    match at {
                        None => handle.move_cursor(None),
                        Some(at) => {
                            if let Some(at) = wire::clamped(at) {
                                handle.move_cursor(Some(at));
                            }
                        }
                    }
                }
            }
            SessionClientMessage::TimeSync { id, client_us } => {
                if cursors.take() {
                    let message = SessionServerMessage::TimeSyncReply {
                        id,
                        client_us,
                        server_us: state.sessions.now_us(),
                    };
                    reply(&replies, message).await?;
                }
            }
            SessionClientMessage::View { seq, view } => views.offer(seq, view),
            SessionClientMessage::CursorOptions(options) => {
                take_command(&mut commands)?;
                handle.set_cursor_options(options);
            }
            SessionClientMessage::Command { seq, cmd } => {
                take_command(&mut commands)?;
                // The seat sent its waiting view first, so it applies first: a `Follow` must not be undone by it.
                if let Some(waiting) = views.flush(Instant::now()) {
                    apply_view(handle, waiting);
                }
                reply(&replies, answer(state, handle, seq, cmd).await).await?;
            }
        }
    }
}

/// Applies a view from the View lane. A refusal settles the view's sequence number inside the session; nothing else
/// to do.
fn apply_view(handle: &Handle<CursorPoint>, (seq, view): (u64, View)) {
    let _ = handle.view(view, seq);
}

/// Hands the writer an answer; it stops listening only when the connection is ending.
async fn reply(
    replies: &mpsc::Sender<SessionServerMessage>,
    message: SessionServerMessage,
) -> Result<(), Exit> {
    replies.send(message).await.map_err(|_| Exit::Gone)
}

fn take_command(commands: &mut Bucket) -> Result<(), Exit> {
    if commands.take() {
        Ok(())
    } else {
        Err(Exit::close(TOO_MANY_COMMANDS, "too many commands"))
    }
}

/// Runs one command and words the result: an `Ack`, or the `Ticket` that answers a ticket request.
async fn answer(
    state: &AppState,
    handle: &Handle<CursorPoint>,
    seq: u64,
    cmd: api_types::SessionCommand,
) -> SessionServerMessage {
    let refused = |code| SessionServerMessage::Ack {
        seq,
        outcome: AckOutcome::Refused { code },
    };
    // Before anything is looked up or validated: a seat without the grant gets the same refusal whatever it sent.
    if let Some(scope) = wire::scope(&cmd)
        && let Err(refusal) = authorize(
            &handle.state(),
            handle.seat(),
            handle.lease(),
            Use::Command(scope),
        )
    {
        return refused(refusal);
    }
    let command = match wire::command(cmd, &state.plugins.handlers(), &state.staged) {
        Ok(Request::Session(command)) => command,
        Ok(Request::Workspace(command)) => {
            let outcome = state.files.run(handle, command).await;
            return SessionServerMessage::Ack { seq, outcome };
        }
        Err(code) => return refused(code),
    };
    if matches!(command, Command::Control(Control::Share { .. }))
        && authorize(
            &handle.state(),
            handle.seat(),
            handle.lease(),
            Use::Command(Scope::Control),
        )
        .is_ok()
    {
        // The link must point somewhere by the time the owner is told sharing is on.
        state.hosting.open().await;
    }
    match handle.command(command) {
        Ok(Reply::Done) => SessionServerMessage::Ack {
            seq,
            outcome: AckOutcome::Ok,
        },
        Ok(Reply::Ticket(ticket)) => SessionServerMessage::Ticket {
            seq,
            ticket: ticket.to_string(),
            expires_ms: u64::try_from(TICKET_TTL.as_millis()).unwrap_or(u64::MAX),
        },
        Err(refusal) => refused(refusal),
    }
}
