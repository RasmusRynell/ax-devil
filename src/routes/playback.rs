//! The playback WebSocket, one per viewer: it opens a video from the workspace, tells the viewer about it, applies
//! the viewer's windows and sends the frames in them. Text messages are JSON (`api_types::playback`); frames and
//! the timeline are binary envelopes (`envelope`).
//!
//! The order of what a viewer receives is `Indexing`*, `Opened`, the Timeline envelope, then frame envelopes and
//! `FrameError`s as they are ready. `Error` is last: the socket closes after it. The viewer is not sent a frame
//! until the previous message has gone to the socket, and at most one more message waits behind it.

mod envelope;

use crate::AppState;
use api_types::playback::{
    ClientMessage, Direction as WireDirection, PLAYBACK_PROTOCOL, ServerMessage, Want as WireWant,
};
use axum::{
    Router,
    extract::{
        State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    response::Response,
    routing::get,
};
use foundation::{CancelToken, Interrupt, UserError};
use futures_util::{
    SinkExt, StreamExt,
    stream::{SplitSink, SplitStream},
};
use playback::{Delivery, Direction, Session, Want};
use std::{path::PathBuf, time::Duration};
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
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
    let ended = run(&state, &mut incoming, &outbox).await;
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

/// Handles what the viewer sent once its video is open: windows apply, anything else ends the session.
fn apply(message: Option<Result<ClientMessage, UserError>>, session: &Session) -> Flow<()> {
    match message {
        Some(Ok(ClientMessage::Want(wire))) => {
            session.want(want(wire));
            Ok(())
        }
        Some(Ok(ClientMessage::Open { .. })) => Err(End::Failed(UserError::new(
            "The video is already open",
            "playback",
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
    incoming: &mut SplitStream<WebSocket>,
    closing: &mut watch::Receiver<bool>,
) -> Flow<()> {
    loop {
        tokio::select! {
            reserved = outbox.reserve() => {
                reserved.map_err(|_| End::Closed)?.send(message);
                return Ok(());
            }
            client = next_client(incoming) => apply(client, session)?,
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

async fn run(state: &AppState, incoming: &mut SplitStream<WebSocket>, outbox: &Outbox) -> Flow<()> {
    let mut closing = state.stopping.clone();
    let first = tokio::select! {
        message = next_client(incoming) => message,
        () = stopped(&mut closing) => None,
    };
    let (content_id, start_frame) = match first {
        Some(Ok(ClientMessage::Open {
            content_id,
            start_frame,
        })) => (content_id, start_frame),
        Some(Err(error)) => return Err(End::Failed(error)),
        Some(Ok(_)) => {
            return Err(End::Failed(UserError::new(
                "The first message must open a video",
                "playback",
            )));
        }
        None => return Err(End::Closed),
    };
    let path = match state.store.snapshot().content(&content_id) {
        Some(Content::SeekableVideo(video)) => video.source.path.clone(),
        None => {
            return Err(End::Failed(UserError::new(
                "This video is not in the workspace",
                content_id,
            )));
        }
    };
    tracing::info!("Opening {} for a viewer", path.display());
    let location = path.display().to_string();
    let mut session = open_video(state, path, incoming, outbox).await?;
    let outcome = stream(
        &mut session,
        &location,
        start_frame,
        incoming,
        outbox,
        &mut closing,
    )
    .await;
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

/// Opens the video on a blocking thread, reporting indexing progress, until the viewer leaves, which cancels it.
async fn open_video(
    state: &AppState,
    path: PathBuf,
    incoming: &mut SplitStream<WebSocket>,
    outbox: &Outbox,
) -> Flow<Session> {
    let cancel = CancelToken::new();
    let _cancel_when_done = CancelOnDrop(cancel.clone());
    let (progress, mut reported) = watch::channel(0.0_f32);
    let location = path.display().to_string();
    let mut job = {
        let videos = state.videos.clone();
        tokio::task::spawn_blocking(move || {
            videos.open(&path, &cancel, &mut |value| {
                progress.send_replace(value);
            })
        })
    };
    let mut closing = state.stopping.clone();
    let mut sent = 0.0_f32;
    loop {
        tokio::select! {
            joined = &mut job => {
                return match joined {
                    Ok(Ok(opened)) => Ok(opened),
                    Ok(Err(Interrupt::Failed(error))) => Err(End::Failed(error)),
                    Ok(Err(Interrupt::Cancelled)) => Err(End::Closed),
                    Err(error) => {
                        tracing::error!("Opening {location} failed: {error}");
                        Err(End::Failed(UserError::new("The video could not be opened", location)))
                    }
                };
            }
            Ok(()) = reported.changed() => {
                let value = *reported.borrow_and_update();
                if value - sent >= PROGRESS_STEP {
                    // Progress is advisory: a viewer that is behind misses a step instead of holding this loop.
                    match outbox.try_send(text(&ServerMessage::Indexing { progress: value })) {
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
                    return Err(End::Failed(UserError::new("A video is already being opened", "playback")));
                }
                Some(Ok(ClientMessage::Close)) | None => return Err(End::Closed),
            },
            () = stopped(&mut closing) => return Err(End::Closed),
        }
    }
}

fn want(wire: WireWant) -> Want {
    Want {
        playhead: wire.playhead,
        ahead: wire.ahead,
        behind: wire.behind,
        direction: match wire.direction {
            WireDirection::Forward => Direction::Forward,
            WireDirection::Backward => Direction::Backward,
        },
    }
}

/// Leaves the source on a blocking thread: the last viewer out frees the whole cache, which is too much to free on
/// an async worker.
fn release(session: Session) {
    drop(tokio::task::spawn_blocking(move || drop(session)));
}

/// Tells the viewer about the video, then applies its windows and sends what it has room for until it leaves.
/// `location` is the video's file, which the errors that end the session name when the layer that failed did not.
async fn stream(
    session: &mut Session,
    location: &str,
    start_frame: Option<u64>,
    incoming: &mut SplitStream<WebSocket>,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
) -> Flow<()> {
    // What the viewer is told comes from the source it joined, which can be older than this open.
    let spec = session.spec();
    let timeline = spec.timeline.clone();
    let last = timeline.last();
    // Decoding starts at the first frame the viewer shows while the messages below are on their way.
    session.want(Want {
        playhead: start_frame.unwrap_or(0).min(last),
        ahead: INITIAL_AHEAD,
        behind: INITIAL_BEHIND,
        direction: Direction::Forward,
    });
    let opened = text(&ServerMessage::Opened {
        protocol: PLAYBACK_PROTOCOL,
        frame_count: timeline.len(),
        duration_us: timeline.duration().as_us(),
        frame_bytes: session.spec().frame_bytes as u64,
    });
    send_watching(outbox, opened, session, incoming, closing).await?;
    let times = Message::Binary(envelope::timeline(&timeline));
    send_watching(outbox, times, session, incoming, closing).await?;

    let mut room = None;
    let mut failures = 0_u32;
    loop {
        tokio::select! {
            message = next_client(incoming) => apply(message, session)?,
            // Room in the outbox is taken before a frame is, so the frame sent is the best one at that moment.
            reserved = outbox.reserve(), if room.is_none() => {
                room = Some(reserved.map_err(|_| End::Closed)?);
            }
            delivery = session.next_delivery(), if room.is_some() => {
                let (Some(delivery), Some(room)) = (delivery, room.take()) else {
                    return Err(End::Closed);
                };
                room.send(encode(delivery, last, location, &mut failures)?);
            }
            () = stopped(closing) => return Err(End::Closed),
        }
    }
}

/// The message for something the session delivers. A source that cannot decode at all ends the session.
fn encode(delivery: Delivery, last: u64, location: &str, failures: &mut u32) -> Flow<Message> {
    match delivery {
        Delivery::Frame(frame) => Ok(Message::Binary(envelope::video_frame(
            &frame,
            frame.id().sequence_id == last,
        ))),
        Delivery::Failure(failure) => {
            // A video with many bad frames would flood the log: the first is a warning, the rest are for diagnosis.
            *failures += 1;
            let error = &failure.error;
            if *failures == 1 {
                tracing::warn!(
                    "Frame {} failed: {}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            } else {
                tracing::debug!(
                    "Frame {} failed: {}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            }
            Ok(text(&ServerMessage::FrameError {
                sequence_id: failure.sequence_id,
                reason: failure.error.reason,
            }))
        }
        Delivery::Failed(mut error) => {
            if error.location.is_empty() {
                error.location = location.to_owned();
            }
            Err(End::Failed(error))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_source_failure_ends_the_session_naming_the_file_unless_the_failure_already_names_one() {
        let ended = |error| match encode(Delivery::Failed(error), 9, "/clips/a.mp4", &mut 0) {
            Err(End::Failed(error)) => error,
            _ => panic!("a failed source must end the session"),
        };

        assert_eq!(
            ended(UserError::new("no decoder", "")).location,
            "/clips/a.mp4"
        );
        assert_eq!(
            ended(UserError::new("no decoder", "/clips/b.mp4")).location,
            "/clips/b.mp4"
        );
    }
}
