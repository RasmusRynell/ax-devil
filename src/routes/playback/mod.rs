//! The playback WebSocket, one per viewer: it opens an Entry of the workspace, tells the viewer about it, applies the
//! viewer's windows and lane views, and sends the frames and the lanes' overlays in them. Text messages are JSON
//! (`api_types::playback`); frames, overlays and the timeline are binary envelopes (`envelope`,
//! `overlay_envelope`).
//!
//! The order of what a viewer receives is `Indexing`*, `Opened`, the Timeline envelope, then frame envelopes,
//! `FrameError`s, `LaneStatus`es, `LaneProblem`s and Overlay envelopes as they are ready; overlay messages go before
//! frames when both wait. `Error` is last: the socket closes after it. The viewer is not sent anything until the
//! previous message has gone to the socket, and at most one more message waits behind it.

mod envelope;
mod lanes;
mod open;
mod overlay_envelope;
mod stream;

use crate::AppState;
use api_types::playback::{ClientMessage, ServerMessage};
use axum::{
    Router,
    extract::{
        State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    response::Response,
    routing::get,
};
use foundation::UserError;
use futures_util::{
    SinkExt, StreamExt,
    stream::{SplitSink, SplitStream},
};
use std::time::Duration;
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
};

/// Client messages are small; anything larger is not this protocol.
const MAX_CLIENT_MESSAGE: usize = 64 * 1024;
/// How long an ending viewer gets to take its last messages, the error that ends it included, before its socket is
/// dropped: a viewer that stopped reading must not keep its socket and tasks for good.
const FLUSH_GRACE: Duration = Duration::from_secs(1);

/// The playback route.
pub fn router() -> Router<AppState> {
    Router::new().route("/api/playback", get(upgrade))
}

async fn upgrade(State(state): State<AppState>, socket: WebSocketUpgrade) -> Response {
    socket
        .max_message_size(MAX_CLIENT_MESSAGE)
        .max_frame_size(MAX_CLIENT_MESSAGE)
        .on_upgrade(move |socket| serve(state, socket))
}

/// Why a session ended.
enum End {
    /// The viewer left, the socket failed or the backend is stopping; there is nobody to tell.
    Closed,
    /// The viewer is told why, in an `Error` message.
    Failed(UserError),
}

type Flow<T> = Result<T, End>;

/// What the writer task sends; the capacity of one makes a send wait for the one before it.
type Outbox = mpsc::Sender<Message>;

async fn serve(state: AppState, socket: WebSocket) {
    let (sink, mut incoming) = socket.split();
    let (outbox, writer) = spawn_writer(sink);
    let abort = writer.abort_handle();
    let ended = open::run(&state, &mut incoming, &outbox).await;
    // One deadline covers telling the viewer why, sending what is queued and closing the socket, so a viewer that
    // stopped reading cannot hold this task, the writer or the socket. Dropping the outbox lets the writer finish.
    let finish = async move {
        if let Err(End::Failed(error)) = ended {
            tracing::warn!("Playback ended: {}: {}", error.location, error.reason);
            let error = ServerMessage::Error {
                reason: error.reason,
                location: error.location,
            };
            let _ = outbox.send(text(&error)).await;
        }
        drop(outbox);
        let _ = writer.await;
    };
    if tokio::time::timeout(FLUSH_GRACE, finish).await.is_err() {
        tracing::debug!("Dropping a playback socket whose viewer stopped reading");
        abort.abort();
    }
}

/// Owns the socket's write half, so the session loop keeps reading while a large frame is being written.
fn spawn_writer(mut sink: SplitSink<WebSocket, Message>) -> (Outbox, JoinHandle<()>) {
    let (outbox, mut queue) = mpsc::channel::<Message>(1);
    let writer = tokio::spawn(async move {
        while let Some(message) = queue.recv().await {
            if sink.send(message).await.is_err() {
                return;
            }
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
async fn next_client(
    incoming: &mut SplitStream<WebSocket>,
) -> Option<Result<ClientMessage, UserError>> {
    loop {
        match incoming.next().await? {
            Ok(Message::Text(body)) => {
                return Some(serde_json::from_str(&body).map_err(|error| {
                    UserError::new(format!("Unreadable message: {error}"), "playback")
                }));
            }
            Ok(Message::Binary(_)) => {
                return Some(Err(UserError::new(
                    "The viewer sent a binary message; only text messages are part of the protocol",
                    "playback",
                )));
            }
            Ok(Message::Ping(_) | Message::Pong(_)) => {}
            Ok(Message::Close(_)) | Err(_) => return None,
        }
    }
}
