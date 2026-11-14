//! Running a plugin's task over the lanes of one entry.
//!
//! The answer is a stream of JSON lines (`TaskLine`): the task's progress, then `done` or `error`. The lanes are opened
//! with `Compute` admission, which an Owner has, written to a folder that lives only as long as the run, and handed
//! to the task as a bundle. Dropping the response cancels the task.

use super::{CancelOnDrop, audience, find_entry, not_found, refuse};
use crate::{AppState, Failure, access::Principal};
use api_types::{
    handlers::{TaskLine, TaskProgressInfo, TaskRunRequest},
    project,
};
use axum::{
    Json,
    body::Body,
    extract::{DefaultBodyLimit, Path, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use bytes::Bytes;
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use futures_util::stream::unfold;
use handler_api::{OverlayRef, Settings, VideoRef};
use plugins::{PluginTask, TaskProgress};
use scene_store::{Admission, build_bundle};
use std::{convert::Infallible, sync::Arc};
use tokio::sync::mpsc;

/// The largest body a run takes: its options.
const MOST_OPTIONS_BYTES: usize = 8 << 20;

/// Lets a run's options be as large as [`MOST_OPTIONS_BYTES`], which a JSON body otherwise may not be.
pub(super) fn limit() -> DefaultBodyLimit {
    DefaultBodyLimit::max(MOST_OPTIONS_BYTES)
}

/// What a run needs, checked: the task, the lanes of the entry by the key it reads them under, the video if it takes
/// one, and its options. The task checks that the lanes are the ones it declares.
struct Prepared {
    task: Arc<PluginTask>,
    lanes: Vec<(String, OverlayRef)>,
    video: Option<VideoRef>,
    options: handler_api::Values,
}

fn prepare(
    state: &AppState,
    id: &str,
    request: TaskRunRequest,
) -> Result<Prepared, (StatusCode, UserError)> {
    let task = state.plugins.task(id).ok_or_else(|| {
        (
            StatusCode::NOT_FOUND,
            UserError::new(ErrorCode::UnknownHandler, "No such task"),
        )
    })?;
    let entry = find_entry(state, &request.item, &request.entry)?;
    let lanes = request
        .lanes
        .into_iter()
        .map(|(key, slot)| {
            let lane = entry.lane(&slot).ok_or_else(|| not_found("lane"))?;
            Ok((key, lane.clone()))
        })
        .collect::<Result<_, _>>()?;
    let form = &task.spec().options;
    let mut options: Settings = request.options;
    // The plugin runs in its own working folder, so a relative path would not mean what it does here.
    workspace::absolute_paths(form, &mut options, &state.plugins.handlers());
    let options = form
        .validate(&options)
        .map_err(|error| (StatusCode::UNPROCESSABLE_ENTITY, error))?;
    Ok(Prepared {
        video: task.spec().inputs.video.then_some(entry.video),
        task,
        lanes,
        options,
    })
}

fn line(line: &TaskLine) -> Bytes {
    let mut bytes = serde_json::to_vec(line).unwrap_or_default();
    bytes.push(b'\n');
    Bytes::from(bytes)
}

/// Opens and writes the lanes into `folder`, then runs the task over them, sending its progress to `lines`.
fn execute(
    state: &AppState,
    run: &Prepared,
    folder: &std::path::Path,
    lines: &mpsc::Sender<Bytes>,
    cancel: &CancelToken,
) -> Result<(), Interrupt<UserError>> {
    let bundle = build_bundle(
        &run.lanes,
        run.video.as_ref(),
        folder,
        |overlay| {
            state
                .overlays
                .open_track(overlay, Admission::Compute, cancel)
        },
        cancel,
    )?;
    let mut on_progress = |progress: TaskProgress| {
        let sent = lines.blocking_send(line(&TaskLine::Progress(TaskProgressInfo {
            done: progress.done,
            total: progress.total,
            text: progress.text,
        })));
        // Nobody reads the answer any more.
        if sent.is_err() {
            cancel.cancel();
        }
    };
    run.task
        .run(&bundle, &run.options, &mut on_progress, cancel)
}

/// Runs the task `id` over the named lanes of an entry and streams its progress. The caller cancels by dropping the
/// request.
pub(super) async fn run(
    principal: Principal,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(request): Json<TaskRunRequest>,
) -> Result<Response, Failure> {
    let audience = audience(principal);
    let run = prepare(&state, &id, request)
        .map_err(|(status, error)| refuse(audience, status, &error))?;
    let (lines, answer) = mpsc::channel::<Bytes>(16);
    let cancel = CancelToken::new();
    let worker_cancel = cancel.clone();
    tokio::task::spawn_blocking(move || {
        let outcome = tempfile::tempdir()
            .map_err(|error| {
                Interrupt::Failed(UserError::new(
                    ErrorCode::Internal,
                    format!("Cannot make a folder for the lanes: {error}"),
                ))
            })
            .and_then(|folder| execute(&state, &run, folder.path(), &lines, &worker_cancel));
        let last = match outcome {
            Ok(()) => TaskLine::Done {},
            Err(Interrupt::Failed(error)) => TaskLine::Error(project(&error, audience)),
            // Only a dropped response cancels a run, so nobody reads this.
            Err(Interrupt::Cancelled) => return,
        };
        let _ = lines.blocking_send(line(&last));
    });
    // The state of the stream owns the guard, so the run is cancelled when the response body is dropped.
    let lines = unfold(
        (answer, CancelOnDrop(cancel)),
        |(mut answer, guard)| async move {
            let next = answer.recv().await?;
            Some((Ok::<_, Infallible>(next), (answer, guard)))
        },
    );
    Ok((
        [
            (header::CONTENT_TYPE, "application/x-ndjson"),
            (header::CACHE_CONTROL, "no-store"),
        ],
        Body::from_stream(lines),
    )
        .into_response())
}
