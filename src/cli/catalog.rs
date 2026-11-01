//! The `catalog` subcommands: what they print. They need no server, settings or log file.

use catalogs::{CatalogDiagnostic, Library};
use std::{fmt::Write, path::Path};

/// What a `catalog` subcommand prints and whether it succeeded.
pub struct Report {
    /// The text to print.
    pub text: String,
    /// Whether the command succeeded; a failed one prints to the error output and exits with a failure.
    pub ok: bool,
}

/// `catalog list`: the built-in catalogs, one per line.
pub fn list(library: &Library) -> Report {
    let infos = library.list();
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

/// `catalog check FILE`: what the catalog says about itself, or every problem in it with its JSON location.
pub fn check(path: &Path) -> Report {
    match catalogs::check_file(path) {
        Ok(metadata) => Report {
            text: format!("{}: {} ({})\n", path.display(), metadata.name, metadata.id),
            ok: true,
        },
        Err(diagnostics) => Report {
            text: problems(path, &diagnostics),
            ok: false,
        },
    }
}

fn problems(path: &Path, diagnostics: &[CatalogDiagnostic]) -> String {
    let mut text = format!("{} has {} problem(s):\n", path.display(), diagnostics.len());
    for diagnostic in diagnostics {
        let location = if diagnostic.location.is_empty() {
            "(document)"
        } else {
            &diagnostic.location
        };
        let _ = writeln!(
            text,
            "  {location}: {} [{}]",
            diagnostic.message, diagnostic.code
        );
    }
    text
}
