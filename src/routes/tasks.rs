//! Running a plugin's task over the lanes of one entry.
//!
//! The answer is a stream of JSON lines (`TaskLine`): the task's progress, then `done` or `error`. The lanes are opened
//! with `Compute` admission, which an Owner has, written to a folder that lives only as long as the run, and handed
//! to the task as a bundle. Dropping the response cancels the task.

use super::CancelOnDrop;
use crate::{AppState, Failure, access::Principal};
use api_types::{
    Audience,
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
use foundation::{CancelToken, ErrorCode, Interrupt, ItemId, UserError};
use futures_util::stream::unfold;
use handler_api::{Bundle, BundleLane, BundleVideo, Settings, VideoRef};
use plugins::{PluginTask, TaskProgress};
use scene_store::{Admission, Ingest, TrackRead, write_lane};
use sessions::SessionId;
use std::{
    collections::BTreeMap,
    convert::Infallible,
    fmt::Write as _,
    fs,
    io::{BufWriter, Write},
    thread,
    time::{Duration, UNIX_EPOCH},
};
use tokio::sync::mpsc;
use workspace::Entry;

/// The largest body a run takes: its options.
const MOST_OPTIONS_BYTES: usize = 8 << 20;

/// How often the wait for a lane to be read looks at the cancel token.
const WAIT_SLICE: Duration = Duration::from_millis(20);

/// Lets a run's options be as large as [`MOST_OPTIONS_BYTES`], which a JSON body otherwise may not be.
pub(super) fn limit() -> DefaultBodyLimit {
    DefaultBodyLimit::max(MOST_OPTIONS_BYTES)
}

/// A refused request: the status and `error` as `audience` may read it.
pub(super) fn refuse(audience: Audience, status: StatusCode, error: &UserError) -> Failure {
    (status, Json(project(error, audience)))
}

/// What an Owner may read of a failure: the host's paths and reasons only with Full access.
pub(super) fn audience(principal: Principal) -> Audience {
    Audience {
        host_details: principal.full_access(),
    }
}

/// The entry `entry` of the item `item`, which the Session `session` must be one that exists.
pub(super) fn find_entry(
    state: &AppState,
    session: &str,
    item: &str,
    entry: &str,
) -> Result<Entry, (StatusCode, UserError)> {
    let missing = |what: &str| {
        (
            StatusCode::NOT_FOUND,
            UserError::new(ErrorCode::Internal, format!("No such {what}")),
        )
    };
    if !session
        .parse::<SessionId>()
        .is_ok_and(|id| state.sessions.subscribe(id).is_some())
    {
        return Err(missing("session"));
    }
    let held = ItemId::parse(item)
        .and_then(|id| state.sessions.find_item(id))
        .ok_or_else(|| missing("item"))?;
    let content = held.content.ok_or_else(|| missing("item"))?;
    content
        .entries()
        .iter()
        .find(|candidate| candidate.id.as_str() == entry)
        .cloned()
        .ok_or_else(|| missing("entry"))
}

fn invalid(reason: impl Into<String>) -> (StatusCode, UserError) {
    (
        StatusCode::UNPROCESSABLE_ENTITY,
        UserError::new(ErrorCode::InvalidRequest, reason),
    )
}

/// What a run needs, checked: the task, the lanes by the key it reads them under, and its options.
struct Prepared {
    task: std::sync::Arc<PluginTask>,
    entry: Entry,
    lanes: BTreeMap<String, String>,
    video: bool,
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
    let entry = find_entry(state, &request.session, &request.item, &request.entry)?;
    let inputs = &task.spec().inputs;
    for (key, _) in &inputs.lanes {
        if !request.lanes.contains_key(key) {
            return Err(invalid(format!("The task needs a lane for '{key}'")));
        }
    }
    for (key, slot) in &request.lanes {
        if !inputs.lanes.iter().any(|(declared, _)| declared == key) {
            return Err(invalid(format!("The task reads no lane '{key}'")));
        }
        if !entry.overlays.iter().any(|overlay| overlay.slot == *slot) {
            return Err(invalid(format!("The entry has no lane '{slot}'")));
        }
    }
    if request.video != inputs.video {
        return Err(invalid(if inputs.video {
            "The task needs the video"
        } else {
            "The task takes no video"
        }));
    }
    let form = &task.spec().options;
    let mut options: Settings = request.options;
    // The plugin runs in its own working folder, so a relative path would not mean what it does here.
    workspace::absolute_paths(form, &mut options, &state.plugins.handlers());
    let options = form
        .validate(&options)
        .map_err(|error| (StatusCode::UNPROCESSABLE_ENTITY, error))?;
    Ok(Prepared {
        task,
        entry,
        lanes: request.lanes,
        video: request.video,
        options,
    })
}

/// One JSON line.
fn line(line: &TaskLine) -> Bytes {
    let mut bytes = serde_json::to_vec(line).unwrap_or_default();
    bytes.push(b'\n');
    Bytes::from(bytes)
}

/// Waits until `track` has read everything it will; a lane that stopped early fails the run.
fn wait_complete(track: &dyn TrackRead, cancel: &CancelToken) -> Result<(), Interrupt<UserError>> {
    loop {
        match &track.snapshot().ingest {
            Ingest::Complete => return Ok(()),
            Ingest::Incomplete(error) => return Err(Interrupt::Failed(error.clone())),
            Ingest::Reading => {}
        }
        cancel.check()?;
        thread::sleep(WAIT_SLICE);
    }
}

/// The bundle entry of the entry's video: a file's size and modification time, or an image sequence's pattern and
/// count.
fn bundle_video(video: &VideoRef) -> Result<BundleVideo, UserError> {
    let failed = |error: std::io::Error| {
        UserError::new(
            ErrorCode::Internal,
            format!("Cannot read the video: {error}"),
        )
    };
    match video {
        VideoRef::File(path) => {
            let path = fs::canonicalize(path).map_err(failed)?;
            let stat = fs::metadata(&path).map_err(failed)?;
            let mtime_ns = stat
                .modified()
                .ok()
                .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
                .and_then(|after| i128::try_from(after.as_nanos()).ok())
                .unwrap_or(0);
            Ok(BundleVideo {
                path,
                size: stat.len(),
                mtime_ns,
            })
        }
        VideoRef::ImageSequence(sequence) => Ok(BundleVideo {
            path: sequence.pattern.clone(),
            size: sequence.frame_count,
            mtime_ns: 0,
        }),
    }
}

/// Opens and writes the lanes into `folder`, then runs the task over them, sending its progress to `lines`.
fn execute(
    state: &AppState,
    run: &Prepared,
    folder: &std::path::Path,
    lines: &mpsc::Sender<Bytes>,
    cancel: &CancelToken,
) -> Result<(), Interrupt<UserError>> {
    let internal = |reason: String| Interrupt::Failed(UserError::new(ErrorCode::Internal, reason));
    let mut bundle = Bundle {
        lanes: BTreeMap::new(),
        video: None,
    };
    for (number, (key, slot)) in run.lanes.iter().enumerate() {
        let overlay = run
            .entry
            .overlays
            .iter()
            .find(|overlay| overlay.slot == *slot)
            .ok_or_else(|| internal(format!("The entry has no lane '{slot}'")))?;
        let track = state
            .overlays
            .open_track(overlay, Admission::Compute, cancel)?;
        wait_complete(&*track, cancel)?;
        let path = folder.join(format!("lane-{number}.jsonl"));
        let file = fs::File::create(&path)
            .map_err(|error| internal(format!("Cannot write a lane: {error}")))?;
        let mut out = BufWriter::new(file);
        let digest = write_lane(&*track, None, u64::MAX, &mut out, cancel)?;
        out.flush()
            .map_err(|error| internal(format!("Cannot write a lane: {error}")))?;
        let sha256 = digest.iter().fold(String::new(), |mut hex, byte| {
            let _ = write!(hex, "{byte:02x}");
            hex
        });
        bundle
            .lanes
            .insert(key.clone(), BundleLane { path, sha256 });
    }
    if run.video {
        bundle.video = Some(bundle_video(&run.entry.video).map_err(Interrupt::Failed)?);
    }
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
