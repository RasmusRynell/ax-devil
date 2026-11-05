//! The backend's Tokio runtime, with a bounded stop.

use std::{future::Future, io, time::Duration};

/// Runs `future` to its end on a new multi-threaded runtime, then stops the runtime and waits at most `grace` for its
/// blocking tasks.
///
/// Dropping a runtime, which `#[tokio::main]` does, waits for every blocking task, and some cannot be told to stop:
/// FFmpeg reading a file on a network mount that stopped answering sits in a system call. Such a task is left
/// behind after `grace` and ends with the process.
pub fn run<F: Future>(future: F, grace: Duration) -> io::Result<F::Output> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let output = runtime.block_on(future);
    runtime.shutdown_timeout(grace);
    Ok(output)
}

/// The open files the backend asks for: every socket [`crate::access::Limits`] admits, with room for the files and
/// decoders beside them. Some systems start a process with 256 (macOS), fewer than the sockets alone.
const OPEN_FILES: u64 = 4096;

/// Raises the soft limit on open files to [`OPEN_FILES`], within the hard limit; a limit that stays below it is
/// logged and kept.
pub fn raise_open_files() {
    #[cfg(unix)]
    {
        use rustix::process::{Resource, getrlimit, setrlimit};
        let mut limit = getrlimit(Resource::Nofile);
        let want = limit.maximum.map_or(OPEN_FILES, |max| max.min(OPEN_FILES));
        if limit.current.is_some_and(|current| current < want) {
            limit.current = Some(want);
            if let Err(error) = setrlimit(Resource::Nofile, limit) {
                tracing::warn!("Cannot raise the open file limit to {want}: {error}");
            }
        }
        if want < OPEN_FILES {
            tracing::warn!(
                "The open file limit is {want}, below {OPEN_FILES}: busy sharing may refuse sockets"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    #[test]
    fn a_blocking_task_that_never_ends_does_not_hold_the_runtime_past_the_grace() {
        let (finished, result) = mpsc::channel();
        let (_keep_blocked, blocked) = mpsc::channel::<()>();
        std::thread::spawn(move || {
            let output = run(
                async move {
                    // Never answered, like a read from a hung mount.
                    tokio::task::spawn_blocking(move || blocked.recv());
                    7
                },
                Duration::from_millis(50),
            );
            finished.send(output.unwrap()).unwrap();
        });

        assert_eq!(result.recv_timeout(Duration::from_secs(10)), Ok(7));
    }
}
