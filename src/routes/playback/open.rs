//! Opening an Entry for a viewer: the first message names it, its video opens while the viewer hears the indexing
//! progress, and the session then streams.

use super::{
    End, Flow, Outbox, lanes, next_client, stopped,
    stream::{self, Link, Setup},
    text,
};
use crate::{AppState, routes::CancelOnDrop, video_sources::OpenedVideo};
use api_types::playback::{ClientMessage, ServerMessage};
use axum::extract::ws::WebSocket;
use foundation::{CancelToken, Interrupt, UserError};
use futures_util::stream::SplitStream;
use playback::Session;
use tokio::sync::{mpsc, watch};
use workspace::VideoRef;

/// Indexing progress is reported at this granularity, so a fast scan sends a hundred messages, not a million.
const PROGRESS_STEP: f32 = 0.01;

/// One session: waits for the viewer to name an Entry, opens it and streams until the session ends.
pub(super) async fn run(
    state: &AppState,
    incoming: &mut SplitStream<WebSocket>,
    outbox: &Outbox,
) -> Flow<()> {
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
    let entry = state.store.snapshot().entry(&content_id).cloned();
    let Some(entry) = entry else {
        return Err(End::Failed(UserError::new(
            "This video is not in the workspace",
            content_id,
        )));
    };
    let location = video_location(&entry.video);
    tracing::info!("Opening {location} for a viewer");
    let opened = open_video(state, entry.video.clone(), incoming, outbox).await?;
    let mut session = opened.session;
    let setup = Setup {
        location,
        lanes: lanes::infos(&entry),
        // A lane without an overlay is the entry's one plain lane.
        sources: if entry.overlays.is_empty() {
            vec![None]
        } else {
            entry.overlays.into_iter().map(Some).collect()
        },
        size: opened.size,
        warnings: opened.warnings,
        start_frame,
        service: state.overlays.clone(),
    };
    let outcome = stream::run(
        &mut session,
        setup,
        Link {
            incoming,
            outbox,
            closing,
        },
    )
    .await;
    release(session);
    outcome
}

/// What errors about `video` name: its file, or the pattern of its images.
fn video_location(video: &VideoRef) -> String {
    match video {
        VideoRef::File(path) => path.display().to_string(),
        VideoRef::ImageSequence(sequence) => sequence.pattern.display().to_string(),
    }
}

/// Opens the video on a blocking thread, reporting indexing progress, until the viewer leaves, which cancels it.
async fn open_video(
    state: &AppState,
    video: VideoRef,
    incoming: &mut SplitStream<WebSocket>,
    outbox: &Outbox,
) -> Flow<OpenedVideo> {
    let cancel = CancelToken::new();
    let _cancel_when_done = CancelOnDrop(cancel.clone());
    let (progress, mut reported) = watch::channel(0.0_f32);
    let location = video_location(&video);
    let mut job = {
        let videos = state.videos.clone();
        tokio::task::spawn_blocking(move || {
            videos.open(&video, &cancel, &mut |value| {
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
                // A viewer sends its windows and lane views once the video is open; one that came early has
                // nothing to window yet.
                Some(Ok(ClientMessage::Want(_) | ClientMessage::LaneView { .. })) => {}
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

/// Leaves the source on a blocking thread: the last viewer out frees the whole cache, which is too much to free on
/// an async worker.
fn release(session: Session) {
    drop(tokio::task::spawn_blocking(move || drop(session)));
}
