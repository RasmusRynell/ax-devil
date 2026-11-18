//! The export job a backend runs: one at a time, on a blocking thread, drawing with the render page.
//!
//! [`ExportService::start`] claims the backend's one export slot, runs `export::run` with a renderer made on the job's
//! own thread, and hands back an [`ExportJob`] that reports progress and the outcome and cancels the job when it is
//! dropped. The playback route owns what a viewer may ask for; this module owns the job.

mod host;

use export::{ExportError, ExportSpec, Renderer, run};
use foundation::{CancelToken, ErrorCode, UserError};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::watch;

/// Progress is reported at most this often.
const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);

/// Makes the renderer of one job, on the job's thread. Once the token is cancelled the renderer ends its waits on the
/// render page with an error instead of finishing them.
pub type RendererFactory =
    Arc<dyn Fn(&CancelToken) -> Result<Box<dyn Renderer>, UserError> + Send + Sync>;

/// Where an export stands.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ExportUpdate {
    /// Frames are being written.
    Running {
        /// Frames written.
        done: u64,
        /// Frames in the range.
        total: u64,
    },
    /// The file is in place.
    Done,
    /// The job was cancelled and nothing was written.
    Cancelled,
    /// The job failed and nothing was written.
    Failed(UserError),
}

impl ExportUpdate {
    /// Whether the job has ended.
    pub fn is_final(&self) -> bool {
        !matches!(self, Self::Running { .. })
    }
}

/// Runs the backend's exports. Cheap to clone; clones share the one slot.
#[derive(Clone)]
pub struct ExportService {
    renderer: RendererFactory,
    busy: Arc<AtomicBool>,
}

impl ExportService {
    /// A service drawing with the render page of the desktop folder's Electron (see [`crate::desktop`]), started when
    /// a job begins.
    pub fn electron() -> Self {
        Self::with_renderer(Arc::new(host::electron))
    }

    /// A service drawing with the renderers `renderer` makes.
    pub fn with_renderer(renderer: RendererFactory) -> Self {
        Self {
            renderer,
            busy: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Starts `spec` on a blocking thread, or says why not: another export is running. Must be called inside a Tokio
    /// runtime.
    pub fn start(&self, spec: ExportSpec) -> Result<ExportJob, UserError> {
        if self.busy.swap(true, Ordering::AcqRel) {
            return Err(UserError::new(
                ErrorCode::Internal,
                "Another export is running; wait for it to finish",
            ));
        }
        let slot = Slot(Arc::clone(&self.busy));
        let cancel = CancelToken::new();
        let total = spec.frames.end() - spec.frames.start() + 1;
        let (updates, receiver) = watch::channel(ExportUpdate::Running { done: 0, total });
        let (renderer, token) = (Arc::clone(&self.renderer), cancel.clone());
        tokio::task::spawn_blocking(move || {
            let outcome = work(&spec, &renderer, &token, &updates);
            // The slot is free before the viewer hears the job ended, so it may start the next one at once.
            drop(slot);
            updates.send_replace(outcome);
        });
        Ok(ExportJob {
            cancel,
            updates: receiver,
        })
    }
}

/// Frees the backend's export slot when the job's thread ends, however it ends.
struct Slot(Arc<AtomicBool>);

impl Drop for Slot {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

/// The job: makes the renderer, runs the export and reports its progress, at most ten times a second.
fn work(
    spec: &ExportSpec,
    renderer: &RendererFactory,
    cancel: &CancelToken,
    updates: &watch::Sender<ExportUpdate>,
) -> ExportUpdate {
    let mut last: Option<Instant> = None;
    let mut progress = |done: u64, total: u64| {
        if last.is_none_or(|at| at.elapsed() >= PROGRESS_INTERVAL) {
            last = Some(Instant::now());
            updates.send_replace(ExportUpdate::Running { done, total });
        }
    };
    let result = renderer(cancel)
        .map_err(ExportError::Failed)
        .and_then(|mut renderer| run(spec, renderer.as_mut(), cancel, &mut progress));
    match result {
        Ok(()) => ExportUpdate::Done,
        Err(ExportError::Cancelled) => ExportUpdate::Cancelled,
        Err(ExportError::Failed(error)) => ExportUpdate::Failed(error),
    }
}

/// A running export. Dropping it cancels the job.
pub struct ExportJob {
    cancel: CancelToken,
    updates: watch::Receiver<ExportUpdate>,
}

impl ExportJob {
    /// Asks the job to stop; its outcome arrives as [`ExportUpdate::Cancelled`], or as the job's own if it was ending.
    pub fn cancel(&self) {
        self.cancel.cancel();
    }

    /// Waits for the job to report something new and returns the latest: a job that reports faster than it is read
    /// is read at its newest, and the outcome is never skipped.
    pub async fn next(&mut self) -> ExportUpdate {
        match self.updates.changed().await {
            Ok(()) => self.updates.borrow_and_update().clone(),
            // The thread ended without an outcome: it panicked.
            Err(_) => ExportUpdate::Failed(UserError::new(
                ErrorCode::Internal,
                "The export stopped unexpectedly",
            )),
        }
    }
}

impl Drop for ExportJob {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}
