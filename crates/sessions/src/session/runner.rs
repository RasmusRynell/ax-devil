//! Running the jobs a session asks for.

use super::{Completion, Spawn, Work};
use foundation::{ErrorCode, Interrupt, UserError};
use handlers::HandlerSet;
use std::sync::{Arc, Mutex};
use tokio::runtime;
use workspace::{Environment, ItemId, Watch};

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

    fn take(&self) -> Read {
        crate::common::locked(&self.0).take()
    }
}

/// Reports a job's result to its session, once.
pub struct Completer {
    pub(super) complete: Box<dyn FnOnce(Completion, Read) + Send>,
    pub(super) watcher: Watcher,
}

impl Completer {
    /// Hands `completion`, with what the job noted it read, to the session's lock. Dropped when the session or the job
    /// is gone or stale.
    pub fn complete(self, completion: Completion) {
        (self.complete)(completion, self.watcher.take());
    }

    /// Where to note what the job read, which may be done from any thread before the job is completed.
    pub fn watcher(&self) -> Watcher {
        self.watcher.clone()
    }
}

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
}

impl Runner for OnRuntime {
    fn run(&self, spawn: Spawn, done: Completer) {
        let handlers = self.handlers.current();
        let env = (self.environment)();
        let watcher = done.watcher();
        self.runtime.spawn(async move {
            if spawn.cancel.is_cancelled() {
                return;
            }
            let Work::Resolve(job) = spawn.work;
            let item = job.item;
            let cancel = spawn.cancel;
            // Resolving reads the file system, which must not block the runtime's workers.
            let resolve = move || {
                let (result, watch) = Watch::read(&job.recipe, &handlers, &cancel, || {
                    workspace_store::resolve(&job, &handlers, &env, &cancel)
                });
                if let Some(watch) = watch {
                    watcher.record(job.item, watch);
                }
                result
            };
            let result = match tokio::task::spawn_blocking(resolve).await {
                Ok(Ok(content)) => Ok(content),
                Ok(Err(Interrupt::Failed(error))) => Err(error),
                // Cancelled because no one wants the result any more.
                Ok(Err(Interrupt::Cancelled)) => return,
                Err(error) => Err(UserError::new(
                    ErrorCode::Internal,
                    format!("Resolving stopped unexpectedly: {error}"),
                )
                .at(item)),
            };
            done.complete(Completion::Resolved(result));
        });
    }
}
