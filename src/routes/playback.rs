//! The playback WebSocket, one per viewer: its first message, within 5 seconds, is `Open{ticket, start_frame}`. The
//! ticket comes from the Session socket and is spent once into a stream lease, which names the video, says what the
//! viewer may ask for and tells when that ends. The route opens the video, tells the viewer about it, applies the
//! viewer's windows and sends the frames in them. Text messages are JSON (`api_types::playback`); frames and the
//! timeline are binary envelopes (`envelope`).
//!
//! A ticket that is unknown, used, expired or not parseable closes the socket with 4401 and nothing else: the viewer
//! learns nothing about why. After that the order of what a viewer receives is `Indexing`*, `Opened`, the Timeline
//! envelope, then frame envelopes and `FrameError`s as they are ready. `Error` is last: the socket closes after it.
//! Errors carry the host's paths and reasons only for the owner of a session that holds the files grant. The viewer
//! is not sent a frame until the previous message has gone to the socket, and at most one more message waits behind
//! it.
//!
//! What a viewer may ask for, and every frame it is sent, is bounded by its lease's `Authority` as it is now
//! (`gate`): when the authority changes or the presenter's clock moves, what the viewer asked is judged again at once
//! and a frame queued outside the new bounds is dropped instead of sent. Once the authority is revoked the session
//! ends at once, also while the video is being indexed, a message still queued is dropped and the socket closes with
//! code 4403.

mod envelope;
mod gate;
#[cfg(test)]
mod revocation;
mod stream;

use crate::{AppState, access::Slot, session::FIRST_MESSAGE_TIMEOUT};
use api_types::playback::{ClientMessage, ServerMessage};
use api_types::{Audience, project};
use axum::{
    extract::{
        ConnectInfo, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::StatusCode,
    response::{IntoResponse, Response},
};
use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use futures_util::{Sink, SinkExt, Stream, StreamExt};
use gate::Gate;
use playback::Session;
use sessions::session::{StreamLease, Ticket};
use std::{net::SocketAddr, time::Duration};
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
    time::timeout,
};
use workspace::Content;

/// Client messages are small; anything larger is not this protocol.
const MAX_CLIENT_MESSAGE: usize = 64 * 1024;
/// Before the viewer's first `Want`, which follows its Timeline within a round trip, it gets this many frames around
/// the one it opened on.
const INITIAL_BEHIND: u32 = 4;
const INITIAL_AHEAD: u32 = 4;
/// How long an ending viewer gets to take its last messages, the error that ends it included, before its socket is
/// dropped: a viewer that stopped reading must not keep its socket and tasks for good.
const FLUSH_GRACE: Duration = Duration::from_secs(1);
/// Indexing progress is reported at this granularity, so a fast scan sends a hundred messages, not a million.
const PROGRESS_STEP: f32 = 0.01;

/// The playback socket upgrade. The socket limits admit it; the ticket in its first message is what authorizes it.
pub(crate) async fn upgrade(
    State(state): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    socket: WebSocketUpgrade,
) -> Response {
    let slot = match state.admission.admit(peer.ip()) {
        Ok(slot) => slot,
        Err(refused) => {
            return (StatusCode::TOO_MANY_REQUESTS, refused.to_string()).into_response();
        }
    };
    socket
        .max_message_size(MAX_CLIENT_MESSAGE)
        .max_frame_size(MAX_CLIENT_MESSAGE)
        .on_upgrade(move |socket| serve(state, socket, slot))
}

/// What a viewer sends: the read half of its socket.
trait Incoming: Stream<Item = Result<Message, axum::Error>> + Unpin {}

impl<T: Stream<Item = Result<Message, axum::Error>> + Unpin> Incoming for T {}

/// Why a session ended.
enum End {
    /// The viewer left, the socket failed or the backend is stopping; there is nobody to tell.
    Closed,
    /// The viewer's authority was revoked; it is told by the close code alone.
    Revoked,
    /// The viewer's ticket is not valid; it is told by the close code alone.
    Refused,
    /// The viewer is told why, in an `Error` message.
    Failed(UserError),
}

type Flow<T> = Result<T, End>;

/// A message for the viewer. A frame says which one it is, so the writer can drop it once the viewer's authority no
/// longer reaches it.
struct Outgoing {
    message: Message,
    frame: Option<u64>,
}

impl From<Message> for Outgoing {
    fn from(message: Message) -> Self {
        Self {
            message,
            frame: None,
        }
    }
}

/// What the writer task sends; the capacity of one makes a send wait for the one before it.
type Outbox = mpsc::Sender<Outgoing>;

async fn serve(state: AppState, socket: WebSocket, mut slot: Slot) {
    let (mut sink, mut incoming) = socket.split();
    let mut closing = state.stopping.clone();
    let opened = tokio::select! {
        opened = open_ticket(&state, &mut slot, &mut incoming) => opened,
        () = stopped(&mut closing) => return,
    };
    let (lease, start_frame) = match opened {
        Ok(opened) => opened,
        Err(ended) => return refuse(&mut sink, ended).await,
    };
    let clock = state.sessions.clone();
    let gate = Gate::new(lease.authority(), move || clock.now_us());
    let audience = Audience {
        host_details: lease.host_details(),
    };
    let (outbox, writer) = spawn_writer(sink, gate.clone());
    let abort = writer.abort_handle();
    let ended = run(
        &state,
        &lease,
        start_frame,
        audience,
        &mut incoming,
        &outbox,
        gate,
    )
    .await;
    // One deadline covers telling the viewer why, sending what is queued and closing the socket, so a viewer that
    // stopped reading cannot hold this task, the writer or the socket. Dropping the outbox lets the writer finish.
    let finish = async move {
        if let Err(End::Failed(error)) = ended {
            tracing::warn!("Playback ended: {:?}: {}", error.location, error.reason);
            let error = ServerMessage::Error(project(&error, audience));
            let _ = outbox.send(text(&error).into()).await;
        }
        drop(outbox);
        let _ = writer.await;
    };
    if tokio::time::timeout(FLUSH_GRACE, finish).await.is_err() {
        tracing::debug!("Dropping a playback socket whose viewer stopped reading");
        abort.abort();
    }
}

/// Reads the viewer's first message and spends its ticket. A ticket that is not valid ends the socket with 4401, and
/// says nothing about why.
async fn open_ticket(
    state: &AppState,
    slot: &mut Slot,
    incoming: &mut impl Incoming,
) -> Flow<(StreamLease, Option<u64>)> {
    let first = timeout(FIRST_MESSAGE_TIMEOUT, next_client(incoming))
        .await
        .map_err(|_| End::Closed)?;
    slot.hello_received();
    let (ticket, start_frame) = match first {
        Some(Ok(ClientMessage::Open {
            ticket,
            start_frame,
        })) => (ticket, start_frame),
        Some(Err(error)) => return Err(End::Failed(error)),
        Some(Ok(_)) => {
            return Err(End::Failed(UserError::new(
                ErrorCode::ProtocolViolation,
                "The first message must open a video",
            )));
        }
        None => return Err(End::Closed),
    };
    let lease = ticket
        .parse::<Ticket>()
        .and_then(|ticket| state.sessions.redeem(&ticket))
        .map_err(|refusal| {
            // Never the ticket itself.
            tracing::debug!("A playback socket's ticket was refused: {refusal}");
            End::Refused
        })?;
    Ok((lease, start_frame))
}

/// Ends a socket that never got a stream: a ticket that is not valid closes it with 4401, a protocol error is
/// answered once, and anything else just closes it.
async fn refuse<S: Sink<Message> + Unpin>(sink: &mut S, ended: End) {
    let last = match ended {
        End::Refused => Some(gate::refused_close()),
        // No stream yet, so the viewer is nobody who may see the host's details.
        End::Failed(error) => Some(text(&ServerMessage::Error(project(
            &error,
            Audience {
                host_details: false,
            },
        )))),
        End::Closed | End::Revoked => None,
    };
    let finish = async {
        if let Some(last) = last {
            let _ = sink.send(last).await;
        }
        let _ = sink.close().await;
    };
    // A viewer that stopped reading does not keep the socket.
    let _ = timeout(FLUSH_GRACE, finish).await;
}

/// Owns the socket's write half, so the session loop keeps reading while a large frame is being written. Whatever is
/// queued when the authority is revoked is dropped, not sent, and the socket closes with the revoked code. A queued
/// frame the authority no longer reaches is dropped too.
fn spawn_writer<S>(mut sink: S, gate: Gate) -> (Outbox, JoinHandle<()>)
where
    S: Sink<Message> + Unpin + Send + 'static,
{
    let (outbox, mut queue) = mpsc::channel::<Outgoing>(1);
    let writer = tokio::spawn(async move {
        while let Some(Outgoing { message, frame }) = queue.recv().await {
            if gate.is_revoked() {
                break;
            }
            if frame.is_some_and(|frame| !gate.admits(frame)) {
                continue;
            }
            if sink.send(message).await.is_err() {
                return;
            }
        }
        if gate.is_revoked() {
            let _ = sink.send(gate::revoked_close()).await;
        }
        let _ = sink.close().await;
    });
    (outbox, writer)
}

/// Handles what the viewer sent once its video is open: windows apply as its authority allows them, anything else
/// ends the session.
fn apply(
    message: Option<Result<ClientMessage, UserError>>,
    session: &Session,
    gate: &mut Gate,
) -> Flow<()> {
    match message {
        Some(Ok(ClientMessage::Want(wire))) => gate.ask(session, stream::want(wire)),
        Some(Ok(ClientMessage::Open { .. })) => Err(End::Failed(UserError::new(
            ErrorCode::ProtocolViolation,
            "The video is already open",
        ))),
        Some(Err(error)) => Err(End::Failed(error)),
        Some(Ok(ClientMessage::Close)) | None => Err(End::Closed),
    }
}

/// Sends `message` once the outbox has room, while still hearing from the viewer and the backend: a viewer that
/// stopped reading cannot keep an ended session waiting.
async fn send_watching(
    outbox: &Outbox,
    message: Message,
    session: &Session,
    gate: &mut Gate,
    incoming: &mut impl Incoming,
    closing: &mut watch::Receiver<bool>,
) -> Flow<()> {
    loop {
        tokio::select! {
            reserved = outbox.reserve() => {
                reserved.map_err(|_| End::Closed)?.send(message.into());
                return Ok(());
            }
            client = next_client(incoming) => apply(client, session, gate)?,
            () = gate.changed() => gate.refresh(session)?,
            () = stopped(closing) => return Err(End::Closed),
        }
    }
}

fn text(message: &ServerMessage) -> Message {
    // Server messages are plain data; encoding one cannot fail.
    Message::text(serde_json::to_string(message).unwrap_or_default())
}

/// Resolves when the backend is stopping.
async fn stopped(closing: &mut watch::Receiver<bool>) {
    let _ = closing.wait_for(|closing| *closing).await;
}

/// The next message of the viewer; `None` when it is gone. Pings and pongs are not messages.
async fn next_client(incoming: &mut impl Incoming) -> Option<Result<ClientMessage, UserError>> {
    loop {
        match incoming.next().await? {
            Ok(Message::Text(body)) => {
                return Some(serde_json::from_str(&body).map_err(|error| {
                    UserError::new(
                        ErrorCode::ProtocolViolation,
                        format!("Unreadable message: {error}"),
                    )
                }));
            }
            Ok(Message::Binary(_)) => {
                return Some(Err(UserError::new(
                    ErrorCode::ProtocolViolation,
                    "The viewer sent a binary message; only text messages are part of the protocol",
                )));
            }
            Ok(Message::Ping(_) | Message::Pong(_)) => {}
            Ok(Message::Close(_)) | Err(_) => return None,
        }
    }
}

/// Opens the lease's video and streams it until the viewer leaves, the backend stops or the authority is revoked.
async fn run(
    state: &AppState,
    lease: &StreamLease,
    start_frame: Option<u64>,
    audience: Audience,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    mut gate: Gate,
) -> Flow<()> {
    let mut closing = state.stopping.clone();
    let Content::SeekableVideo(video) = lease.content();
    let location = video.source.path.clone();
    tracing::info!("Opening {} for a viewer", location.as_path().display());
    let path = location.as_path().to_path_buf();
    let videos = state.videos.clone();
    let mut session = open_video(
        move |cancel, progress| videos.open(&path, cancel, progress),
        &location,
        incoming,
        outbox,
        &mut closing,
        &mut gate,
    )
    .await?;
    let viewer = stream::Viewer {
        location,
        start_frame,
        audience,
    };
    let outcome = stream::stream(&mut session, &viewer, incoming, outbox, &mut closing, gate).await;
    release(session);
    outcome
}

/// Cancels the index build of a viewer that left.
struct CancelOnDrop(CancelToken);

impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}

/// Runs `open` on a blocking thread, reporting indexing progress, until the viewer leaves or its authority is revoked,
/// which cancels it. `location` is the video's file.
async fn open_video(
    open: impl FnOnce(&CancelToken, &mut dyn FnMut(f32)) -> Result<Session, Interrupt<UserError>>
    + Send
    + 'static,
    location: &HostPath,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
    gate: &mut Gate,
) -> Flow<Session> {
    let cancel = CancelToken::new();
    let _cancel_when_done = CancelOnDrop(cancel.clone());
    let (progress, mut reported) = watch::channel(0.0_f32);
    let mut job = tokio::task::spawn_blocking(move || {
        open(&cancel, &mut |value| {
            progress.send_replace(value);
        })
    });
    let mut sent = 0.0_f32;
    loop {
        tokio::select! {
            joined = &mut job => {
                return match joined {
                    Ok(Ok(opened)) => Ok(opened),
                    Ok(Err(Interrupt::Failed(error))) => Err(End::Failed(error)),
                    Ok(Err(Interrupt::Cancelled)) => Err(End::Closed),
                    Err(error) => {
                        tracing::error!("Opening {} failed: {error}", location.as_path().display());
                        Err(End::Failed(
                            UserError::new(ErrorCode::Internal, "The video could not be opened")
                                .at(location.clone()),
                        ))
                    }
                };
            }
            Ok(()) = reported.changed() => {
                let value = *reported.borrow_and_update();
                if value - sent >= PROGRESS_STEP {
                    // Progress is advisory: a viewer that is behind misses a step instead of holding this loop.
                    match outbox.try_send(text(&ServerMessage::Indexing { progress: value }).into()) {
                        Ok(()) => sent = value,
                        Err(mpsc::error::TrySendError::Full(_)) => {}
                        Err(mpsc::error::TrySendError::Closed(_)) => return Err(End::Closed),
                    }
                }
            }
            message = next_client(incoming) => match message {
                // A viewer sends its windows once the video is open; one that came early has nothing to window yet.
                Some(Ok(ClientMessage::Want(_))) => {}
                Some(Err(error)) => return Err(End::Failed(error)),
                Some(Ok(ClientMessage::Open { .. })) => {
                    return Err(End::Failed(UserError::new(
                        ErrorCode::ProtocolViolation,
                        "A video is already being opened",
                    )));
                }
                Some(Ok(ClientMessage::Close)) | None => return Err(End::Closed),
            },
            () = stopped(closing) => return Err(End::Closed),
            () = gate.revoked() => return Err(End::Revoked),
        }
    }
}

/// Leaves the source on a blocking thread: the last viewer out frees the whole cache, which is too much to free on
/// an async worker.
fn release(session: Session) {
    drop(tokio::task::spawn_blocking(move || drop(session)));
}
