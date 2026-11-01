//! The threads of a viewer: one loader per overlay file, and one worker that compiles catalogs and prepares frames.

use foundation::{ErrorCode, Interrupt, UserError};
use playlist_resolvers::OverlayRef;

use crate::{
    prepare::prepare,
    sources::{CatalogSource, TrackSource},
    state::{Job, Outcome, Shared, Task, describe},
};

/// Closes the viewer when its thread ends, however it ends, so a consumer waiting in `next_event` is released. A
/// viewer still open then fails its lanes first: the worker died, and their overlays will not come.
struct CloseOnExit<'a>(&'a Shared);

impl Drop for CloseOnExit<'_> {
    fn drop(&mut self) {
        self.0.abort(&UserError::new(
            ErrorCode::Internal,
            "The overlays stopped unexpectedly",
        ));
        self.0.close();
    }
}

/// Loads one lane's track and records how it went. A cancelled load records nothing.
pub(crate) fn load(shared: &Shared, tracks: &dyn TrackSource, lane: usize, overlay: &OverlayRef) {
    let result = match tracks.open(overlay, shared.frame_size, &shared.cancel) {
        Ok(track) => Ok(track),
        Err(Interrupt::Cancelled) => return,
        Err(Interrupt::Failed(error)) => Err(error),
    };
    shared.update(|state| state.finish_load(lane, result));
}

/// The worker loop: the most urgent task, until the viewer closes.
pub(crate) fn run(shared: &Shared, catalogs: &dyn CatalogSource) {
    let _close = CloseOnExit(shared);
    while let Some(task) = next_task(shared) {
        match task {
            Task::Compile { lane, id } => {
                let result = catalogs.compiled(&id);
                shared.update(|state| state.finish_compile(lane, id, result));
            }
            Task::Prepare(job) => {
                let outcome = prepare_job(shared, &job);
                shared.update(|state| state.finish_prepare(&job, outcome));
            }
        }
    }
}

/// Waits for a task; `None` once the viewer is closed.
fn next_task(shared: &Shared) -> Option<Task> {
    let mut state = shared.lock();
    loop {
        if state.closed {
            return None;
        }
        if let Some(task) = state.next_task(&shared.timeline) {
            return Some(task);
        }
        state = shared
            .work
            .wait(state)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
    }
}

/// Prepares a job. A frame that cannot be prepared still gets an empty overlay and a problem, so the viewer is not
/// held up waiting for it.
fn prepare_job(shared: &Shared, job: &Job) -> Outcome {
    let catalog = job.catalog.as_deref();
    match prepare(&job.track, &shared.timeline, &job.view, catalog, job.frame) {
        Ok(prepared) => Outcome {
            selection: prepared.selection,
            drawing: prepared.drawing,
            problems: prepared
                .problems
                .into_iter()
                .map(|problem| {
                    if problem.location.is_empty() {
                        problem.message
                    } else {
                        format!("{} ({})", problem.message, problem.location)
                    }
                })
                .collect(),
        },
        Err(error) => Outcome {
            selection: overlay_sync::Selection::None,
            drawing: drawing::DrawingInstructions::default(),
            problems: vec![describe(&error)],
        },
    }
}
