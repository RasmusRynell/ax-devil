//! The share socket: one WebSocket per seat, from `Hello` to the close. The sessions crate decides what each role may
//! do; this adapter decides who may be seated, keeps the connection honest (limits, liveness, deadlines) and speaks
//! the wire protocol.
//!
//! Close codes: 1000 the client left, 1007 not a valid message, 1008 not allowed, 1009 frame too big, 1013 full,
//! 4401 invalid password or link, 4404 session ended or unknown, 4409 seat replaced, 4429 too many messages. The
//! client treats 1007, 1008, 1009, 1013, 4401, 4404, 4409 and 4429 as final and reports them; any other close, or a
//! connection that drops without one, is retried with the seat's resume token. (A 4404 answering a resume of a seat
//! the server no longer holds is retried once with the way in the client entered by.)

use crate::{
    AppState,
    access::{Principal, Slot},
    share_state::{self, ShareConfig},
    share_wire::{self, Bucket, Incoming, Seat, Shown},
};
use ::sessions::{
    Connection, EndReason, JoinLink, Role, Secret, SessionError, SessionId, Update, Violation,
};
use api_types::{CursorPoint, Grant, ShareAccess, ShareClientMessage, ShareServerMessage};
use axum::{
    Router,
    extract::{
        ConnectInfo, State, WebSocketUpgrade,
        ws::{CloseFrame, Message, WebSocket},
    },
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use futures_util::{Sink, SinkExt, StreamExt, stream::SplitStream};
use std::{convert::Infallible, net::SocketAddr, time::Duration};
use tokio::time::{Instant, MissedTickBehavior, interval_at, timeout, timeout_at};

const INVALID_DATA: u16 = 1007;
const NOT_ALLOWED: u16 = 1008;
const TOO_BIG: u16 = 1009;
const FULL: u16 = 1013;
const INVALID_CREDENTIAL: u16 = 4401;
const ENDED: u16 = 4404;
const REPLACED: u16 = 4409;
const TOO_MANY_MESSAGES: u16 = 4429;

const MAX_FRAME_BYTES: usize = 16 * 1024;
const MAX_MESSAGE_BYTES: usize = 64 * 1024;
const CURSORS_PER_SECOND: u32 = 60;
const MESSAGES_PER_SECOND: u32 = 10;

/// The share socket alone, which every listener serves.
pub(crate) fn socket_routes() -> Router<AppState> {
    Router::new().route("/api/share/socket", get(socket))
}

/// The share socket and the session list, for listeners whose callers may hold the Sessions grant.
pub(crate) fn routes() -> Router<AppState> {
    socket_routes().route("/api/share/sessions", get(share_state::list))
}

async fn socket(
    State(state): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    upgrade: WebSocketUpgrade,
) -> Response {
    let slot = match state.admission.admit(peer.ip()) {
        Ok(slot) => slot,
        Err(refused) => {
            return (StatusCode::TOO_MANY_REQUESTS, refused.to_string()).into_response();
        }
    };
    upgrade
        .max_frame_size(MAX_FRAME_BYTES)
        .max_message_size(MAX_MESSAGE_BYTES)
        .on_upgrade(move |socket| async move {
            // An upgraded connection would hold the backend's shutdown open, so the socket ends with the backend.
            let mut stopping = state.stopping.clone();
            tokio::select! {
                () = run(state, slot, socket) => {}
                _ = stopping.wait_for(|stopping| *stopping) => {}
            }
        })
}

/// How a socket ends.
enum Exit {
    /// The peer closed, vanished, stalled or went silent: nobody is left to tell.
    Gone,
    /// The client left.
    Left,
    /// Close with this code.
    Close(u16, String),
    /// The seat's session is over.
    Ended(EndReason),
}

impl Exit {
    fn close(code: u16, reason: &str) -> Self {
        Self::Close(code, reason.to_owned())
    }
}

/// Runs one socket until it ends. Dropping the seat at the end is the disconnect that starts its grace period.
async fn run(state: AppState, mut slot: Slot, socket: WebSocket) {
    let (mut sink, mut stream) = socket.split();
    let config = state.share.config;
    let (seat, principal) = match hello(&state, &mut slot, &mut stream).await {
        Ok(admitted) => admitted,
        Err(exit) => return finish(&mut sink, exit, config.send_deadline).await,
    };
    let connection = connection(&seat);
    let welcome = welcome(&state, &seat, principal);
    let result = tokio::select! {
        result = reader(&mut stream, &seat, &config) => result,
        result = writer(&state, &mut sink, welcome, connection) => result,
    };
    let Err(exit) = result;
    if matches!(exit, Exit::Left) {
        match seat {
            Seat::Owner(owner) => owner.leave(),
            Seat::Watcher(watcher) => watcher.leave(),
        }
    }
    finish(&mut sink, exit, config.send_deadline).await;
}

fn connection(seat: &Seat) -> &Connection<Shown, CursorPoint> {
    match seat {
        Seat::Owner(owner) => owner,
        Seat::Watcher(watcher) => watcher,
    }
}

/// Reads `Hello` and seats the socket.
async fn hello(
    state: &AppState,
    slot: &mut Slot,
    stream: &mut SplitStream<WebSocket>,
) -> Result<(Seat, Principal), Exit> {
    let config = &state.share.config;
    let first = timeout(config.hello_timeout, async {
        loop {
            match stream.next().await {
                Some(Ok(Message::Ping(_) | Message::Pong(_))) => {}
                other => return other,
            }
        }
    })
    .await
    .map_err(|_| Exit::close(NOT_ALLOWED, "no hello in time"))?;
    slot.hello_received();
    let text = match first {
        Some(Ok(Message::Text(text))) => text,
        Some(Ok(Message::Binary(_))) => return Err(Exit::close(INVALID_DATA, "expected text")),
        _ => return Err(Exit::Gone),
    };
    let Ok(ShareClientMessage::Hello {
        access,
        server_password,
        cursors,
    }) = serde_json::from_str(&text)
    else {
        return Err(Exit::close(INVALID_DATA, "expected hello"));
    };
    let principal = state.policy.principal_for(server_password.as_deref());
    if server_password.is_some() && principal == Principal::None {
        return Err(Exit::close(INVALID_CREDENTIAL, "invalid password"));
    }
    let cursors = share_wire::cursor_options(cursors);
    let sessions = &state.share.sessions;
    let seat = match access {
        ShareAccess::Create => {
            require_sessions(principal)?;
            let owner = state
                .share
                .create(cursors)
                .ok_or_else(|| Exit::close(FULL, "this server has too many sessions"))?;
            state.hosting.open().await;
            Seat::Owner(owner)
        }
        ShareAccess::Watch { session } => {
            require_sessions(principal)?;
            let id = session
                .parse::<SessionId>()
                .map_err(|error| refuse(&error, ENDED))?;
            Seat::Watcher(
                sessions
                    .watch(id, cursors)
                    .map_err(|error| refuse(&error, ENDED))?,
            )
        }
        ShareAccess::Join { link } => {
            let link = link
                .parse::<JoinLink>()
                .map_err(|error| refuse(&error, INVALID_CREDENTIAL))?;
            Seat::Watcher(
                sessions
                    .join(&link, cursors)
                    .map_err(|error| refuse(&error, INVALID_CREDENTIAL))?,
            )
        }
        ShareAccess::Resume { token } => {
            let token = token
                .parse::<Secret>()
                .map_err(|error| refuse(&error, ENDED))?;
            let seat = sessions
                .resume(&token)
                .map_err(|error| refuse(&error, ENDED))?;
            // The client sent its current choices; they replace the ones the seat had when it dropped, before
            // the first snapshot is built.
            connection(&seat).set_cursor_options(cursors);
            seat
        }
    };
    Ok((seat, principal))
}

fn require_sessions(principal: Principal) -> Result<(), Exit> {
    if principal.grants().contains(&Grant::Sessions) {
        Ok(())
    } else {
        Err(Exit::close(NOT_ALLOWED, "this needs the server password"))
    }
}

/// `malformed` is the code for text that is not an id, link or token: what the caller was trying to present.
fn refuse(error: &SessionError, malformed: u16) -> Exit {
    let code = match error {
        SessionError::Malformed => malformed,
        SessionError::NotFound => ENDED,
        SessionError::Rejected => INVALID_CREDENTIAL,
        SessionError::Full => FULL,
    };
    Exit::Close(code, error.to_string())
}

/// What the seat is told first. The join link goes to owners and to holders of the Sessions grant only.
fn welcome(state: &AppState, seat: &Seat, principal: Principal) -> ShareServerMessage {
    let connection = connection(seat);
    let (role, may_see_link) = match seat {
        Seat::Owner(_) => (api_types::SeatRole::Owner, true),
        Seat::Watcher(_) => (api_types::SeatRole::Watcher, principal != Principal::None),
    };
    let link = may_see_link
        .then(|| {
            state
                .share
                .sessions
                .list()
                .into_iter()
                .find(|summary| summary.id == connection.session())
                .and_then(|summary| state.hosting.join_url(&summary.link))
        })
        .flatten();
    ShareServerMessage::Welcome {
        session: connection.session().to_string(),
        you: connection.participant().0,
        role,
        resume: connection.resume_token().to_string(),
        link,
    }
}

/// Applies what the seat sends, until it leaves, breaks a rule or goes quiet.
async fn reader(
    stream: &mut SplitStream<WebSocket>,
    seat: &Seat,
    config: &ShareConfig,
) -> Result<Infallible, Exit> {
    let mut cursors = Bucket::new(CURSORS_PER_SECOND);
    let mut others = Bucket::new(MESSAGES_PER_SECOND);
    let mut heard = Instant::now();
    loop {
        let item = timeout_at(heard + config.silence_timeout, stream.next())
            .await
            .map_err(|_| Exit::Gone)?;
        heard = Instant::now();
        let text = match item {
            None | Some(Ok(Message::Close(_))) => return Err(Exit::Gone),
            Some(Err(_)) => return Err(Exit::close(TOO_BIG, "invalid or oversized frame")),
            Some(Ok(Message::Text(text))) => text,
            Some(Ok(Message::Binary(_))) => return Err(Exit::close(INVALID_DATA, "expected text")),
            // Pings are answered by the socket itself; both only show the peer is alive.
            Some(Ok(Message::Ping(_) | Message::Pong(_))) => continue,
        };
        let message = serde_json::from_str::<ShareClientMessage>(&text)
            .map_err(|error| Exit::Close(INVALID_DATA, error.to_string()))?;
        match share_wire::incoming(message) {
            Incoming::Hello => return Err(Exit::close(NOT_ALLOWED, "hello was already sent")),
            Incoming::Leave => return Err(Exit::Left),
            Incoming::Ignore => {}
            Incoming::Seat(message) => {
                let is_cursor = matches!(message, ::sessions::SeatMessage::Cursor(_));
                let allowed = if is_cursor {
                    cursors.take()
                } else {
                    others.take()
                };
                if !allowed {
                    if is_cursor {
                        continue;
                    }
                    return Err(Exit::close(TOO_MANY_MESSAGES, "too many messages"));
                }
                // The others only lost a race with the owner's options or an earlier message.
                if seat.apply(message) == Err(Violation::Role) {
                    return Err(Exit::close(NOT_ALLOWED, "your role cannot do that"));
                }
            }
        }
    }
}

/// Sends what the seat should see: the welcome, its snapshots, others' cursors, and the pings and heartbeats that
/// show the link is up. It never waits on the reader, so a stalled send cannot hold up incoming messages.
async fn writer<S: Sink<Message> + Unpin>(
    state: &AppState,
    sink: &mut S,
    welcome: ShareServerMessage,
    connection: &Connection<Shown, CursorPoint>,
) -> Result<Infallible, Exit> {
    let config = &state.share.config;
    let deadline = config.send_deadline;
    let mut snapshots = connection.snapshots();
    let mut cursors = connection.cursors();
    send(sink, &welcome, deadline).await?;
    let mut link = match snapshots.current() {
        Update::Snapshot(personal) => {
            send(
                sink,
                &ShareServerMessage::Snapshot(share_wire::snapshot(&personal)),
                deadline,
            )
            .await?;
            personal.link
        }
        Update::Ended(reason) => return Err(Exit::Ended(reason)),
    };
    let ticker = |period: Duration| {
        let mut ticker = interval_at(Instant::now() + period, period);
        ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
        ticker
    };
    let mut heartbeat = ticker(config.heartbeat_interval);
    let mut ping = ticker(config.ping_interval);
    let mut cursors_open = true;
    loop {
        tokio::select! {
            update = snapshots.next() => match update {
                Update::Snapshot(personal) => {
                    if personal.link != link {
                        link = personal.link.clone();
                        if let Some(url) = link.as_ref().and_then(|link| state.hosting.join_url(link)) {
                            send(sink, &ShareServerMessage::Link { url }, deadline).await?;
                        }
                    }
                    send(
                        sink,
                        &ShareServerMessage::Snapshot(share_wire::snapshot(&personal)),
                        deadline,
                    )
                    .await?;
                }
                Update::Ended(reason) => return Err(Exit::Ended(reason)),
            },
            event = cursors.recv(), if cursors_open => match event {
                Some(event) => {
                    let message = ShareServerMessage::Cursor {
                        participant: event.participant.0,
                        at: event.at,
                    };
                    send(sink, &message, deadline).await?;
                }
                // The session ended; its snapshot feed says so.
                None => cursors_open = false,
            },
            _ = heartbeat.tick() => send(sink, &ShareServerMessage::Heartbeat, deadline).await?,
            _ = ping.tick() => send_frame(sink, Message::Ping(Vec::new().into()), deadline).await?,
        }
    }
}

async fn send<S: Sink<Message> + Unpin>(
    sink: &mut S,
    message: &ShareServerMessage,
    deadline: Duration,
) -> Result<(), Exit> {
    let text = serde_json::to_string(message).map_err(|error| {
        tracing::error!("Cannot encode a share message: {error}");
        Exit::Gone
    })?;
    send_frame(sink, Message::Text(text.into()), deadline).await
}

/// One send, which must finish within `deadline`: a peer that stopped reading counts as gone.
async fn send_frame<S: Sink<Message> + Unpin>(
    sink: &mut S,
    frame: Message,
    deadline: Duration,
) -> Result<(), Exit> {
    match timeout(deadline, sink.send(frame)).await {
        Ok(Ok(())) => Ok(()),
        Ok(Err(_)) | Err(_) => Err(Exit::Gone),
    }
}

/// Tells the peer why the socket closes, as far as it will listen.
async fn finish<S: Sink<Message> + Unpin>(sink: &mut S, exit: Exit, deadline: Duration) {
    let (code, reason) = match exit {
        Exit::Gone => return,
        Exit::Left => (1000, String::new()),
        Exit::Close(code, reason) => (code, reason),
        Exit::Ended(reason) => {
            let message = ShareServerMessage::Ended {
                reason: share_wire::ended(reason),
            };
            if send(sink, &message, deadline).await.is_err() {
                return;
            }
            let code = if reason == EndReason::Replaced {
                REPLACED
            } else {
                ENDED
            };
            (code, "the session is over".to_owned())
        }
    };
    // A close reason holds at most 123 bytes.
    let mut reason = reason;
    let end = (0..=reason.len().min(120))
        .rev()
        .find(|end| reason.is_char_boundary(*end))
        .unwrap_or(0);
    reason.truncate(end);
    let frame = Message::Close(Some(CloseFrame {
        code,
        reason: reason.into(),
    }));
    // The peer may already be gone.
    let _ = send_frame(sink, frame, deadline).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_send_that_never_finishes_counts_as_a_disconnect() {
        let mut stalled = Box::pin(futures_util::sink::unfold((), |(), _: Message| {
            std::future::pending::<Result<(), Infallible>>()
        }));
        let sent = send(
            &mut stalled,
            &ShareServerMessage::Heartbeat,
            Duration::from_millis(50),
        )
        .await;
        assert!(matches!(sent, Err(Exit::Gone)));
    }
}
