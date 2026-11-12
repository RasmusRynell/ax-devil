//! The playback WebSocket, one per viewer: its first message, within 5 seconds, is `Open`. The
//! ticket comes from the Session socket and is spent once into a stream lease, which names the entry, says what the
//! viewer may ask for and tells when that ends. The route opens the entry's video and overlays, tells the viewer
//! about them, applies the viewer's windows and lane policies and sends the frames in the windows and the lanes'
//! data for them. Text messages are JSON (`api_types::playback`); frames, the timeline, Sample Items and Frame Maps
//! are binary envelopes (`api_types::playback::envelope`).
//!
//! A ticket that is unknown, used, expired or not parseable closes the socket with 4401 and nothing else: the viewer
//! learns nothing about why. After that the order of what a viewer receives is `Indexing`*, `Opened`, the Timeline
//! envelope, then frame envelopes, `FrameError`s, `LaneStatus`es, `LaneProblem`s, `Catalog` answers and pushes,
//! `Catalogs` lists, `Cached` ranges, `DataWindow`s, `Evict`s and Sample and FrameMap envelopes as they are ready. Notices go first, then the `DataWindow` and the
//! `Evict`s, which are never held back or dropped for a frame; a Sample or FrameMap goes after them, and before a
//! frame when its rank (`playback::rank` of the frame it is for) is at most the rank of the frame waiting, so data
//! wins ties and the frames nearest the playhead are not held up by data far from it. `Error` is last: the socket closes after it. Errors,
//! warnings and lane problems carry the host's paths and reasons only for the owner of a session that holds the
//! files grant. The viewer is not sent anything until the previous message has gone to the socket, and at most one
//! more message waits behind it.
//!
//! What a viewer may ask for, and every frame, Sample Item and Frame Map it is sent, is bounded by its lease's `Authority` as
//! it is now (`gate`): when the authority changes or the presenter's clock moves, what the viewer asked is judged
//! again at once, the data follows the window the gate allowed, and a frame or data message queued with any frame outside
//! the new bounds is dropped instead of sent; dropped data is offered again if the window still needs it. Once the authority is revoked the session ends at once, also while the video
//! is being indexed, its overlays stop, a message still queued is dropped and the socket closes with code 4403.
//!
//! The owner can ask for rows of a lane's whole file (`file_scope`): the answers name the revisions they were made
//! under and no frame, so they are never held back, and a follower is told it follows.
//!
//! A live lease is served by `live`: it has no Timeline, window or export, and only its owner may pause, resume or
//! retry it.
//!
//! The owner can start an export (`export`): one per viewer and one per backend, which ends with the viewer. Its
//! progress notices name no frame and are never held back.

mod cached;
mod catalogs;
mod delivery;
mod export;
mod file_scope;
mod gate;
mod inspect;
mod lanes;
mod live;
mod open;
mod prepare;
#[cfg(test)]
mod revocation;
mod stream;
mod wire;

pub(crate) use lanes::policy_from_wire;

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
use foundation::{ErrorCode, UserError};
use futures_util::{Sink, SinkExt, Stream, StreamExt};
use gate::Gate;
use overlays::OverlayEvent;
use sessions::session::{StreamLease, Ticket};
use std::{net::SocketAddr, ops::RangeInclusive, time::Duration};
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
    time::timeout,
};

/// Client messages are small; anything larger is not this protocol.
const MAX_CLIENT_MESSAGE: usize = api_types::inspect::MAX_INSPECTION_REQUEST_BYTES;
/// How long an ending viewer gets to take its last messages, the error that ends it included, before its socket is
/// dropped: a viewer that stopped reading must not keep its socket and tasks for good.
const FLUSH_GRACE: Duration = Duration::from_secs(1);

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

/// What the viewer's `Open` asks for besides the stream.
#[derive(Clone, Copy)]
struct Opening {
    /// The frame to show first.
    start_frame: Option<u64>,
    /// The most bytes of Sample Items the viewer holds at once.
    data_bytes: u64,
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

/// A message for the viewer. A frame, or the data of frames (a sample for the frame it ranks by, a map run for the
/// frames it covers), says which, so the writer can drop it once the viewer's authority no longer reaches all of
/// them. Dropped data is told to the gate, which hands it back to the overlays to offer again.
struct Outgoing {
    message: Message,
    frames: Option<RangeInclusive<u64>>,
    data: Option<OverlayEvent>,
    inspection: Option<foundation::CancelToken>,
}

impl Outgoing {
    /// A frame, or what is about one.
    fn frame(message: Message, frame: u64) -> Self {
        Self {
            message,
            frames: Some(frame..=frame),
            data: None,
            inspection: None,
        }
    }
}

impl From<Message> for Outgoing {
    fn from(message: Message) -> Self {
        Self {
            message,
            frames: None,
            data: None,
            inspection: None,
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
    let (lease, opening) = match opened {
        Ok(opened) => opened,
        Err(ended) => return refuse(&mut sink, ended).await,
    };
    let clock = state.sessions.clone();
    let gate = Gate::new(lease.authority(), move || clock.now_us())
        .with_inspection(lease.inspection_permission());
    let audience = Audience {
        host_details: lease.host_details(),
    };
    let (outbox, writer) = spawn_writer(sink, gate.clone());
    let abort = writer.abort_handle();
    let ended = open::run(
        &state,
        &lease,
        opening,
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
) -> Flow<(StreamLease, Opening)> {
    let first = timeout(FIRST_MESSAGE_TIMEOUT, next_client(incoming))
        .await
        .map_err(|_| End::Closed)?;
    slot.hello_received();
    let (ticket, opening) = match first {
        Some(Ok(ClientMessage::Open {
            ticket,
            start_frame,
            data_bytes,
        })) => (
            ticket,
            Opening {
                start_frame,
                data_bytes,
            },
        ),
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
    Ok((lease, opening))
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
/// frame, sample or map run the authority no longer reaches is dropped too, and the overlays are told to offer it
/// again if the viewer still needs it.
fn spawn_writer<S>(mut sink: S, gate: Gate) -> (Outbox, JoinHandle<()>)
where
    S: Sink<Message> + Unpin + Send + 'static,
{
    let (outbox, mut queue) = mpsc::channel::<Outgoing>(1);
    let writer = tokio::spawn(async move {
        while let Some(Outgoing {
            message,
            frames,
            data,
            inspection,
        }) = queue.recv().await
        {
            // Wait for socket readiness before the final validity/authority checks, then submit without awaiting.
            if std::future::poll_fn(|cx| sink.poll_ready_unpin(cx))
                .await
                .is_err()
            {
                return;
            }
            if gate.is_revoked() {
                break;
            }
            if inspection.is_some_and(|validity| validity.is_cancelled() || !gate.can_inspect()) {
                continue;
            }
            if frames.is_some_and(|frames| !gate.admits(&frames)) {
                if let Some(data) = data {
                    gate.reject(data);
                }
                continue;
            }
            if sink.start_send_unpin(message).is_err() || sink.flush().await.is_err() {
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
