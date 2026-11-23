//! What the registry and the session service share: the lock and the grace timer.

use std::{
    sync::{Mutex, MutexGuard, PoisonError},
    time::Duration,
};
use tokio::runtime::Handle;

/// Locks `mutex`. Every change completes before the lock is released, so a panic elsewhere leaves the state
/// consistent and a poisoned lock is still safe to use.
pub(crate) fn locked<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Runs `then` once `delay` has passed. Without a runtime no timer can run, so the delay cannot be kept and `then`
/// runs at once.
pub(crate) fn after(delay: Duration, then: impl FnOnce() + Send + 'static) {
    match Handle::try_current() {
        Ok(runtime) => {
            runtime.spawn(async move {
                tokio::time::sleep(delay).await;
                then();
            });
        }
        Err(_) => then(),
    }
}
