//! Opening the lease's entry for a viewer: its video opens while the viewer hears the indexing progress, and the
//! session then streams.

use super::{
    End, Flow, Incoming, Opening, Outbox,
    gate::Gate,
    live, next_client, stopped,
    stream::{self, Setup},
    text,
    wire::Wire,
};
use crate::{
    AppState,
    video_sources::{OpenedVideo, video_location},
};
use api_types::{
    Audience,
    playback::{ClientMessage, ServerMessage},
};
use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use playback::Session;
use sessions::session::StreamLease;
use tokio::sync::{mpsc, watch};
use workspace::Content;

/// Indexing progress is reported at this granularity, so a fast scan sends a hundred messages, not a million.
const PROGRESS_STEP: f32 = 0.01;

/// Opens the lease's entry and streams it until the viewer leaves, the backend stops or the authority is revoked.
pub(super) async fn run(
    state: &AppState,
    lease: &StreamLease,
    opening: Opening,
    audience: Audience,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    mut gate: Gate,
) -> Flow<()> {
    let mut closing = state.stopping.clone();
    let entry = match (lease.content(), lease.entry()) {
        (Content::LiveVideo(live), _) => {
            return live::run(state, lease, live, audience, incoming, outbox, gate).await;
        }
        (_, Some(entry)) => entry,
        (_, None) => {
            return Err(End::Failed(UserError::new(
                ErrorCode::Internal,
                "The stream has no entry",
            )));
        }
    };
    let location = video_location(&entry.video);
    tracing::info!("Opening {} for a viewer", location.as_path().display());
    let (videos, video) = (state.videos.clone(), entry.video.clone());
    let opened = open_video(
        move |cancel, progress| videos.open(&video, cancel, progress),
        &location,
        incoming,
        outbox,
        &mut closing,
        &mut gate,
    )
    .await?;
    let OpenedVideo {
        mut session,
        size,
        warnings,
        pts,
        cadence,
    } = opened;
    let setup = Setup {
        entry,
        matching: lease.matching(),
        start_frame: opening.start_frame,
        data_bytes: opening.data_bytes,
        audience,
        session: lease.session(),
        size,
        wire: Wire::of(&state.policy),
        warnings,
        pts,
        cadence,
        library: &state.catalogs,
        overlays: &state.overlays,
        exports: &state.exports,
        videos: &state.videos,
    };
    let outcome = stream::stream(&mut session, setup, incoming, outbox, &mut closing, gate).await;
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
/// which cancels it. `location` is the video's file, or the pattern of its images.
pub(super) async fn open_video<T: Send + 'static>(
    open: impl FnOnce(&CancelToken, &mut dyn FnMut(f32)) -> Result<T, Interrupt<UserError>>
    + Send
    + 'static,
    location: &HostPath,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
    gate: &mut Gate,
) -> Flow<T> {
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
                // A viewer sends its windows, lane policies and catalog requests once the video is open; one that
                // came early has nothing to apply to yet.
                Some(Ok(
                    ClientMessage::Want(_)
                    | ClientMessage::LanePolicy { .. }
                    | ClientMessage::Catalog { .. }
                    | ClientMessage::Inspect { .. }
                    | ClientMessage::InspectFields { .. }
                    | ClientMessage::Export { .. }
                    | ClientMessage::CancelExport { .. }
                    | ClientMessage::FileQuery { .. }
                    | ClientMessage::Appearances { .. }
                    | ClientMessage::EventQuery(_)
                    | ClientMessage::Event(_)
                    | ClientMessage::FileRelease { .. }
                    | ClientMessage::Live { .. },
                )) => {}
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
