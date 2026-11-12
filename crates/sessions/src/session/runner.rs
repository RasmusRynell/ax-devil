//! Running the jobs a session asks for.

use super::{Completion, Spawn, Work};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handlers::HandlerSet;
use std::sync::{Arc, Mutex};
use tokio::runtime;
use workspace::{Content, ContentId, Environment, ItemId, VideoRef, Watch};

/// Runs the jobs a session asks for. The session does not care how: a runner starts the work and reports its
/// [`Completion`] through the [`Completer`], now or later, on any thread.
pub trait Runner: Send + Sync + 'static {
    /// Starts `spawn`. Work that sees `spawn.cancel` cancelled may stop and report nothing.
    fn run(&self, spawn: Spawn, done: Completer);
}

impl<F: Fn(Spawn, Completer) + Send + Sync + 'static> Runner for F {
    fn run(&self, spawn: Spawn, done: Completer) {
        self(spawn, done);
    }
}

/// What a job read, held until the job is completed: the session records it only together with an accepted result, so
/// a job that was superseded meanwhile never replaces the sources of the current one.
#[derive(Clone, Default)]
pub struct Watcher(Arc<Mutex<Read>>);

/// What a job noted it read: the item and what it was read from.
pub(super) type Read = Option<(ItemId, Watch)>;

impl Watcher {
    /// Notes that `item` was read from what `watch` says, to be recorded when the job's result is accepted.
    pub fn record(&self, item: ItemId, watch: Watch) {
        *crate::common::locked(&self.0) = Some((item, watch));
    }

    pub(super) fn take(&self) -> Read {
        crate::common::locked(&self.0).take()
    }
}

/// Offers a job's result to its session, retaining completion ownership when another round is needed.
pub struct Completer {
    pub(super) complete: Box<dyn FnOnce(Completion, Watcher) -> Result<(), Retry> + Send>,
    pub(super) watcher: Watcher,
}

// Retry transfers the single resolved Content by value between rounds, without an extra heap owner.
#[allow(clippy::result_large_err)]
impl Completer {
    /// Hands `completion`, with what the job noted it read, to the session's lock. Dropped when the session or the job
    /// is gone or stale; otherwise returns the entries still to validate when coverage is incomplete.
    pub fn complete(self, completion: Completion) -> Result<(), Retry> {
        (self.complete)(completion, self.watcher)
    }

    /// Where to note what the job read, which may be done from any thread before the job is completed.
    pub fn watcher(&self) -> Watcher {
        self.watcher.clone()
    }
}

/// A resolved reload that needs more media validated before it can commit.
pub struct Retry {
    /// Completes the same registered job, retaining its watch record.
    pub done: Completer,
    /// The single resolved Content retained across validation rounds.
    pub content: Content,
    /// Entries already covered by successful checks or absent from the replacement.
    pub validated: Vec<ContentId>,
    /// Newly shown entries still to check.
    pub missing: Vec<ContentId>,
}

type OpenMedia = dyn Fn(&VideoRef, &CancelToken) -> Result<(), Interrupt<UserError>> + Send + Sync;

/// A [`Runner`] that resolves items on a tokio runtime's blocking threads, with the installed handlers for
/// playlists and overlays.
pub struct OnRuntime {
    /// Where the jobs run.
    pub runtime: runtime::Handle,
    /// The resolvers and decoders recipes name; each job takes the handlers as they are when it starts.
    pub handlers: Arc<HandlerSet>,
    /// Where a live stream's `$NAME` references get their values; each job takes the environment as it is when it
    /// starts.
    pub environment: Arc<dyn Fn() -> Environment + Send + Sync>,
    /// Checks that replacement media opens before a reload replaces the shown Content.
    pub opens: Arc<OpenMedia>,
}

impl Runner for OnRuntime {
    fn run(&self, spawn: Spawn, done: Completer) {
        let handlers = self.handlers.current();
        let env = if spawn.expands {
            (self.environment)()
        } else {
            Environment::literal()
        };
        let watcher = done.watcher();
        let opens = Arc::clone(&self.opens);
        self.runtime.spawn(async move {
            if spawn.cancel.is_cancelled() {
                return;
            }
            let Work::Resolve { job, shown } = spawn.work;
            let item = job.item;
            let cancel = spawn.cancel;
            let checking = cancel.clone();
            let opening = Arc::clone(&opens);
            // Resolving and checking media read the file system off the runtime's workers.
            let mut work = tokio::task::spawn_blocking(move || {
                let (result, watch) = Watch::read(&job.recipe, &handlers, &checking, || {
                    workspace::store::resolve(&job, &handlers, &env, &checking)
                });
                if let Some(watch) = watch {
                    watcher.record(job.item, watch);
                }
                validate(result?, Vec::new(), shown, &*opening, &checking)
            });
            let mut done = done;
            loop {
                let (result, validated) = match work.await {
                    Ok(Ok((content, validated))) => (Ok(content), validated),
                    Ok(Err(Interrupt::Failed(error))) => (Err(error), Vec::new()),
                    Ok(Err(Interrupt::Cancelled)) => return,
                    Err(error) => (
                        Err(UserError::new(
                            ErrorCode::Internal,
                            format!("Resolving stopped unexpectedly: {error}"),
                        )
                        .at(item)),
                        Vec::new(),
                    ),
                };
                match done.complete(Completion::Resolved { result, validated }) {
                    Ok(()) => return,
                    Err(retry) => {
                        done = retry.done;
                        let checking = cancel.clone();
                        let opening = Arc::clone(&opens);
                        work = tokio::task::spawn_blocking(move || {
                            validate(
                                retry.content,
                                retry.validated,
                                retry.missing,
                                &*opening,
                                &checking,
                            )
                        });
                    }
                }
            }
        });
    }
}

fn validate(
    content: Content,
    mut validated: Vec<ContentId>,
    missing: Vec<ContentId>,
    opens: &OpenMedia,
    cancel: &CancelToken,
) -> Result<(Content, Vec<ContentId>), Interrupt<UserError>> {
    for id in missing {
        cancel.check()?;
        if let Some(entry) = content.entry(&id) {
            opens(&entry.video, cancel)?;
        }
        validated.push(id);
    }
    Ok((content, validated))
}
