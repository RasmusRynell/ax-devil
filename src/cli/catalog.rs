//! The commands that only print, `list-handlers` and `catalog`: what they print. They need no server, settings or
//! log file.

use super::{CatalogCommand, args, desktop};
use catalogs::Library;
use handlers::Handlers;
use std::{
    fmt::Write,
    path::Path,
    process::{Command as Process, ExitCode},
};

/// What a `catalog` subcommand prints and whether it succeeded.
#[derive(Debug)]
pub struct Report {
    /// The text to print.
    pub text: String,
    /// Whether the command succeeded; a failed one prints to the error output and exits with a failure.
    pub ok: bool,
}

impl Report {
    /// `list-handlers`: the decoders and resolvers `handlers` holds.
    pub fn listing(handlers: &Handlers) -> Self {
        Self {
            text: args::handlers_listing(handlers),
            ok: true,
        }
    }

    /// Prints the report, to the error output when it failed, and gives the exit code.
    #[allow(clippy::print_stdout, clippy::print_stderr)]
    pub fn print(self) -> ExitCode {
        if self.ok {
            print!("{}", self.text);
            ExitCode::SUCCESS
        } else {
            eprint!("{}", self.text);
            ExitCode::FAILURE
        }
    }
}

/// What a `catalog` subcommand prints.
pub fn run(command: &CatalogCommand) -> Report {
    match command {
        CatalogCommand::List => list(&Library::built_in()),
        CatalogCommand::Check { file } => check(file),
    }
}

/// `catalog list`: the built-in catalogs the app serves, one per line.
pub fn list(library: &Library) -> Report {
    let infos = library.documents();
    let width = infos
        .iter()
        .map(|info| info.id.to_string().len())
        .max()
        .unwrap_or(0);
    let mut text = String::new();
    for info in &infos {
        let _ = writeln!(
            text,
            "{:<width$}  {}: {}",
            info.id.to_string(),
            info.name,
            info.description
        );
    }
    Report { text, ok: true }
}

/// `catalog check FILE`: what the catalog says about itself, or every problem in it with its location.
pub fn check(path: &Path) -> Report {
    match desktop::folder()
        .map_err(|error| error.to_string())
        .and_then(|desktop| check_in(&desktop, path))
    {
        Ok(report) => report,
        Err(reason) => Report {
            text: format!("{reason}\n"),
            ok: false,
        },
    }
}

/// Runs the frontend's catalog compiler on `path` in Electron-as-Node, from the desktop folder `desktop`. The
/// compiler reads the file and writes the report; this only starts it.
pub fn check_in(desktop: &Path, path: &Path) -> Result<Report, String> {
    let electron = desktop::electron(desktop)?;
    let checker = desktop.join("generated/catalog-check.cjs");
    if !checker.is_file() {
        return Err(format!(
            "Cannot find the catalog checker at {}; build it with pnpm --dir frontend build",
            checker.display()
        ));
    }
    let output = Process::new(electron)
        .env("ELECTRON_RUN_AS_NODE", "1")
        .arg(checker)
        .arg(path)
        .output()
        .map_err(|error| format!("Cannot start Electron to check the catalog: {error}"))?;
    let mut text = String::from_utf8_lossy(&output.stdout).into_owned();
    if text.is_empty() {
        text = String::from_utf8_lossy(&output.stderr).into_owned();
    }
    Ok(Report {
        text,
        ok: output.status.success(),
    })
}
