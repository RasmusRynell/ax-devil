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
