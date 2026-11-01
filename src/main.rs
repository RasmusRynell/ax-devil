//! Starts ax-devil as the command line says; see `cli.rs`.

mod cli;

use std::{process::ExitCode, time::Duration};

/// How long the runtime waits for blocking work, such as indexing a video, once everything else has stopped.
const RUNTIME_GRACE: Duration = Duration::from_secs(1);

fn main() -> ExitCode {
    match ax_devil::runtime::run(cli::run(), RUNTIME_GRACE) {
        Ok(code) => code,
        Err(error) => {
            // Logging starts inside `cli::run`, which never ran.
            #[allow(clippy::print_stderr)]
            {
                eprintln!("ax-devil: cannot start the runtime: {error}");
            }
            ExitCode::FAILURE
        }
    }
}
