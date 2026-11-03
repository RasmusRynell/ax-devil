//! Running the jobs a session asks for.

use super::{Completion, Spawn, Work};
use foundation::{ErrorCode, Interrupt, UserError};
use handlers::HandlerSet;
use std::sync::Arc;
use tokio::runtime;

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

/// Reports a job's result to its session, once.
pub struct Completer(pub(super) Box<dyn FnOnce(Completion) + Send>);

impl Completer {
    /// Hands `completion` to the session's lock. Dropped when the session or the job is gone or stale.
    pub fn complete(self, completion: Completion) {
        (self.0)(completion);
    }
}

/// A [`Runner`] that resolves items on a tokio runtime's blocking threads, with the installed handlers for
/// playlists and overlays.
pub struct OnRuntime {
    /// Where the jobs run.
    pub runtime: runtime::Handle,
    /// The resolvers and decoders recipes name; each job takes the handlers as they are when it starts.
    pub handlers: Arc<HandlerSet>,
}

impl Runner for OnRuntime {
    fn run(&self, spawn: Spawn, done: Completer) {
        let handlers = self.handlers.current();
        self.runtime.spawn(async move {
            if spawn.cancel.is_cancelled() {
                return;
            }
            let Work::Resolve(job) = spawn.work;
            let item = job.item;
            let cancel = spawn.cancel;
            // Resolving reads the file system, which must not block the runtime's workers.
            let resolve = move || workspace_store::resolve(&job, &handlers, &cancel);
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
