//! The commands that only print, `list-handlers` and `catalog`: what they print. They need no server or log file.

use super::args;
use api_types::handlers::PluginsInfo;
use ax_devil::desktop;
use catalogs::{CatalogId, Library, Served, sheets};
use clap::{Subcommand, ValueHint};
use handlers::Handlers;
mod manage;
mod running;
pub use manage::Home;
pub use running::use_default;
use std::{
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process::{Command as Process, ExitCode},
};

/// What `catalog` was asked to do.
#[derive(Subcommand)]
pub enum CatalogCommand {
    /// List the built-in catalogs and the files of the user's catalog folder; * marks the default for new lanes.
    List,
    /// Validate a catalog and draw the example sheets with it; every problem is listed with its JSON location.
    ///
    /// Without a catalog, the default for new lanes is checked, even when it is broken.
    Check {
        /// A catalog file, `built-in:<name>`, or a catalog of the user's folder as `user:<file name>`.
        #[arg(value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        file: Option<PathBuf>,
    },
    /// Print the catalog language: document keys, marks, types, functions, fields and limits.
    ///
    /// The text is generated from the tables the catalog compiler checks documents with.
    Reference,
    /// Save a copy of a catalog in the user's folder as NAME and print its file; the default does not change.
    ///
    /// A catalog that is not built in is checked first. A catalog file from anywhere is copied in.
    New {
        /// The name of the new catalog.
        name: String,
        /// The catalog to copy: `built-in:<name>`, `user:<file name>`, or a catalog file.
        #[arg(long = "from", value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        from: Option<PathBuf>,
    },
    /// Make a catalog the default for new lanes: `built-in:<name>`, `user:<file name>`, or a file in the catalog folder.
    ///
    /// A catalog that is not built in is checked first; it must be one the folder lists. Uses the local backend on
    /// $PORT (default 7330) when its configuration identity matches; writes the file only for an absent or confirmed different backend.
    /// A refusal never falls back to disk.
    Use {
        /// The catalog.
        #[arg(value_name = "CATALOG", value_hint = ValueHint::FilePath)]
        catalog: PathBuf,
    },
}

/// What a `catalog` subcommand prints and whether it succeeded.
#[derive(Debug)]
pub struct Report {
    /// The text to print.
    pub text: String,
    /// Whether the command succeeded; a failed one prints to the error output and exits with a failure.
    pub ok: bool,
}

impl Report {
    /// `list-handlers`: the decoders and resolvers `handlers` holds, and the plugins that did not load.
    pub fn listing(handlers: &Handlers, installed: &PluginsInfo) -> Self {
        Self {
            text: args::handlers_listing(handlers, installed),
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

/// What a `catalog` subcommand prints. `home` is the user's catalog folder and settings, or why they are not known; a
/// command that names a catalog file by its path works without them.
pub fn run(command: &CatalogCommand, home: Result<Home, String>) -> Report {
    let outcome = match command {
        CatalogCommand::List => home.map(|home| manage::list(&home)),
        CatalogCommand::Check { file } => check_command(file.as_deref(), home),
        CatalogCommand::Reference => Ok(reference()),
        CatalogCommand::New { name, from } => {
            home.and_then(|home| manage::new(&home, name, from.as_deref()))
        }
        CatalogCommand::Use { catalog } => {
            home.and_then(|home| manage::use_catalog(&home, catalog))
        }
    };
    outcome.unwrap_or_else(|reason| Report {
        text: format!("{reason}\n"),
        ok: false,
    })
}

/// A catalog to check: one built into the app, or a file.
pub enum Target {
    /// The built-in catalog of this name, checked from the document embedded in the app.
    BuiltIn(&'static str),
    /// A catalog file.
    File(PathBuf),
}

/// What `file` names: a built-in as `built-in:<name>`, a catalog of the user's folder as `user:<file name>`, else
/// the file at that path.
fn target_of(file: &Path, home: &Result<Home, String>) -> Result<Target, String> {
    match file.to_str().and_then(CatalogId::parse) {
        Some(CatalogId::BuiltIn(name)) => Ok(Target::BuiltIn(name)),
        Some(id) => home
            .as_ref()
            .map(|home| home.target(id))
            .map_err(Clone::clone),
        None => Ok(Target::File(file.to_path_buf())),
    }
}

/// The text of `catalog reference`, generated by `scripts/catalog-reference.ts`; the artifact's header says so and is
/// not printed.
const REFERENCE: &str = include_str!("catalog_reference.txt");

/// `catalog reference`: the catalog language.
pub fn reference() -> Report {
    let (_header, text) = REFERENCE.split_once("\n\n").unwrap_or(("", REFERENCE));
    Report {
        text: text.to_owned(),
        ok: true,
    }
}

/// What `file` names, with its id when it is a catalog of the app or the folder, or the default for new lanes, even
/// when that one is broken.
fn selected(
    file: Option<&Path>,
    home: &Result<Home, String>,
) -> Result<(Option<CatalogId>, Target), String> {
    match file {
        Some(file) => Ok((
            file.to_str().and_then(CatalogId::parse),
            target_of(file, home)?,
        )),
        None => {
            let home = home.as_ref().map_err(Clone::clone)?;
            let id = home.default_id(&Library::with_folder(home.folder.clone()))?;
            let target = home.target(id.clone());
            Ok((Some(id), target))
        }
    }
}

/// `catalog check [FILE]`: the catalog `file` names, or the default for new lanes, even when that one is broken. A
/// catalog of the user's folder is checked only if the library serves it.
fn check_command(file: Option<&Path>, home: Result<Home, String>) -> Result<Report, String> {
    let (id, target) = selected(file, &home)?;
    match (id, &home) {
        (Some(id @ CatalogId::User(_)), Ok(home)) => {
            manage::check_listed(home, &Library::with_folder(home.folder.clone()), &id)
        }
        _ => check_target(&target),
    }
}

/// What the catalog says about itself, or every problem in it with its location.
pub fn check_target(target: &Target) -> Result<Report, String> {
    let desktop = desktop::folder().map_err(|error| error.to_string())?;
    check_target_in(&desktop, target)
}

/// [`check_target`] with the desktop folder `desktop`.
pub fn check_target_in(desktop: &Path, target: &Target) -> Result<Report, String> {
    through_file(target, |path| check_in(desktop, path))
}

/// Runs `run` on the catalog file of `target`. A built-in is written to a scratch file, which the checker needs, and the report names the catalog by its id instead.
pub(super) fn through_file(
    target: &Target,
    run: impl FnOnce(&Path) -> Result<Report, String>,
) -> Result<Report, String> {
    match target {
        Target::File(path) => run(path),
        Target::BuiltIn(name) => {
            let id = CatalogId::BuiltIn(name);
            let Served::Document { text, .. } = Library::built_in().document(&id) else {
                return Err(format!("{id} is not a catalog of this app"));
            };
            let scratch = tempfile::tempdir()
                .map_err(|error| format!("Cannot make a folder to read {id} in: {error}"))?;
            let file = scratch.path().join(format!("{name}.json"));
            fs::write(&file, text.as_bytes())
                .map_err(|error| format!("Cannot write {}: {error}", file.display()))?;
            let mut report = run(&file)?;
            report.text = report
                .text
                .replace(&file.display().to_string(), &id.to_string());
            Ok(report)
        }
    }
}

/// Checks the catalog `path` in the desktop folder `desktop`: the compiler, then the example sheet to draw it on. The
/// report lists the problems of a catalog that does not compile, or of drawing the sheet with one that does.
pub fn check_in(desktop: &Path, path: &Path) -> Result<Report, String> {
    let scratch = tempfile::tempdir()
        .map_err(|error| format!("Cannot make a folder for the example sheet: {error}"))?;
    let file = scratch.path().join("overview");
    fs::write(&file, sheets::overview()?)
        .map_err(|error| format!("Cannot write {}: {error}", file.display()))?;
    let mut argument = OsString::from("overview=");
    argument.push(file);
    run_checker(desktop, path, &[argument])
}

/// Runs the frontend's catalog checker on `path` in Electron-as-Node with the `NAME=PATH` sheets. The checker reads
/// the files and writes the report; this only starts it.
fn run_checker(desktop: &Path, path: &Path, sheets: &[OsString]) -> Result<Report, String> {
    let electron = desktop::electron(desktop).map_err(|path| desktop::missing_electron(&path))?;
    let checker = desktop::catalog_checker(desktop)?;
    let output = Process::new(electron)
        .env("ELECTRON_RUN_AS_NODE", "1")
        .arg(checker)
        .arg(path)
        .args(sheets)
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
