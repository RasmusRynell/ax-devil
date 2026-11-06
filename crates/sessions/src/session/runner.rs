//! Running the jobs a session asks for.

use super::{Completion, Spawn, Work};
use foundation::{ErrorCode, Interrupt, UserError};
use handlers::HandlerSet;
use std::sync::Arc;
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

/// Records what an item of the job's session was read from, so a change to it is noticed.
#[derive(Clone)]
pub struct Watcher(pub(super) Arc<dyn Fn(ItemId, Watch) + Send + Sync>);

impl Watcher {
    /// Records that `item` was read from what `watch` says, replacing what was recorded for it.
    pub fn record(&self, item: ItemId, watch: Watch) {
        (self.0)(item, watch);
    }
}

/// Reports a job's result to its session, once.
pub struct Completer {
    pub(super) complete: Box<dyn FnOnce(Completion) + Send>,
    pub(super) watcher: Watcher,
}

impl Completer {
    /// Hands `completion` to the session's lock. Dropped when the session or the job is gone or stale.
    pub fn complete(self, completion: Completion) {
        (self.complete)(completion);
    }

    /// Where to record what the job read, which may be done from any thread before the job is completed.
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
    /// Where a live stream's `$NAME` references get their values and which the user confirmed; each job takes the
    /// environment as it is when it starts, so a confirmation reaches the next `Resolve`.
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
