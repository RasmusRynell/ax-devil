//! `clear-cache`: lists the cache files, asks, removes them and says how much it removed.

use ax_devil::disk_cache;
use clap::ValueEnum;
use std::{
    io::{self, BufRead, Write},
    path::Path,
    process::ExitCode,
};

/// How the cache is listed before it is cleared. Both scopes clear every cache file ax-devil owns.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, ValueEnum)]
pub enum Scope {
    /// The cache folder as one location.
    Main,
    /// Each kind of cache file in its own folder, then the total.
    #[default]
    All,
}

/// Clears the cache folder `cache_dir` (or reports why it is not known), asking on the standard input unless `force`.
pub fn run(cache_dir: Result<std::path::PathBuf, String>, scope: Scope, force: bool) -> ExitCode {
    let result = cache_dir.and_then(|folder| {
        clear(
            &folder,
            scope,
            force,
            &mut io::stdin().lock(),
            &mut io::stdout().lock(),
        )
    });
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(reason) => {
            // The command's own output: the logger is not started for commands that only print.
            let _ = writeln!(io::stderr(), "{reason}");
            ExitCode::FAILURE
        }
    }
}

/// The whole conversation: `Err` carries the reasons for a failure, one per line.
fn clear(
    folder: &Path,
    scope: Scope,
    force: bool,
    input: &mut impl BufRead,
    out: &mut impl Write,
) -> Result<(), String> {
    let found = disk_cache::scan(folder)?;
    for folder in &found.skipped {
        say(
            out,
            format!("{}: not a plain folder, skipped", folder.display()),
        )?;
    }
    if found.file_count() == 0 {
        let none = match scope {
            Scope::All => "No cached files found to clear.",
            Scope::Main => "No cached files found for cache type 'main'.",
        };
        return say(out, none.into());
    }
    say(out, "Cache locations that will be cleared:".into())?;
    match scope {
        Scope::Main => say(out, row(folder, found.file_count(), found.bytes()))?,
        Scope::All => {
            for location in &found.locations {
                let (count, bytes) = (location.file_count(), location.bytes());
                say(out, row(&location.folder, count, bytes))?;
            }
            let (count, bytes) = (found.file_count(), found.bytes());
            say(out, format!("Total: {} ({})", files(count), size(bytes)))?;
        }
    }
    if !force {
        say(
            out,
            "Are you sure you want to clear these caches? [y/N]: ".into(),
        )?;
        let mut answer = String::new();
        input.read_line(&mut answer).map_err(|e| e.to_string())?;
        if !matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
            return say(out, "Cache clearing cancelled.".into());
        }
    }
    let cleared = disk_cache::clear(&found.locations);
    say(
        out,
        format!(
            "Removed {} ({}).",
            files(cleared.files),
            size(cleared.bytes)
        ),
    )?;
    if cleared.failures.is_empty() {
        Ok(())
    } else {
        Err(cleared.failures.join("\n"))
    }
}

/// One listed location: `Empty`, or its files and their size.
fn row(folder: &Path, count: usize, bytes: u64) -> String {
    if count == 0 {
        format!("{}: Empty", folder.display())
    } else {
        format!("{}: {} ({})", folder.display(), files(count), size(bytes))
    }
}

fn say(out: &mut impl Write, line: String) -> Result<(), String> {
    // The prompt stays on its line, so the answer is typed after it.
    let end = if line.ends_with(' ') { "" } else { "\n" };
    write!(out, "{line}{end}")
        .and_then(|()| out.flush())
        .map_err(|error| error.to_string())
}

fn files(count: usize) -> String {
    format!("{count} {}", if count == 1 { "file" } else { "files" })
}

/// A size in binary units: `512 B`, `1.5 KiB`, `3.0 MiB`.
fn size(bytes: u64) -> String {
    let mut value = bytes as f64;
    let mut unit = "B";
    for next in ["KiB", "MiB", "GiB", "TiB"] {
        if value < 1024.0 {
            break;
        }
        value /= 1024.0;
        unit = next;
    }
    if unit == "B" {
        format!("{bytes} B")
    } else {
        format!("{value:.1} {unit}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_with(folder: &Path, force: bool, answer: &str) -> (Result<(), String>, String) {
        run_scoped(folder, Scope::All, force, answer)
    }

    fn run_scoped(
        folder: &Path,
        scope: Scope,
        force: bool,
        answer: &str,
    ) -> (Result<(), String>, String) {
        let mut out = Vec::new();
        let result = clear(folder, scope, force, &mut answer.as_bytes(), &mut out);
        (result, String::from_utf8(out).unwrap())
    }

    #[test]
    fn the_scope_changes_the_listing_and_both_clear_the_same_files_once() {
        let cache = tempfile::tempdir().unwrap();
        let write = |path: &str, bytes: usize| {
            let path = cache.path().join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, vec![0; bytes]).unwrap();
        };
        write("frame-index/a.axidx", 100);
        write("scenes/b.axscene", 28);
        std::fs::create_dir(cache.path().join("thumbnails")).unwrap();
        let at = |name: &str| cache.path().join(name).display().to_string();

        let (_, listed) = run_scoped(cache.path(), Scope::All, false, "n\n");
        assert_eq!(
            listed,
            format!(
                "Cache locations that will be cleared:\n{}: 1 file (100 B)\n{}: 1 file (28 B)\n{}: Empty\n\
                 Total: 2 files (128 B)\nAre you sure you want to clear these caches? [y/N]: Cache clearing cancelled.\n",
                at("frame-index"),
                at("scenes"),
                at("thumbnails"),
            )
        );
        let (_, listed) = run_scoped(cache.path(), Scope::Main, true, "");
        assert_eq!(
            listed,
            format!(
                "Cache locations that will be cleared:\n{}: 2 files (128 B)\nRemoved 2 files (128 B).\n",
                cache.path().display()
            )
        );
        let (_, listed) = run_scoped(cache.path(), Scope::Main, true, "");
        assert_eq!(listed, "No cached files found for cache type 'main'.\n");
    }

    #[test]
    fn nothing_is_removed_unless_the_answer_is_yes_or_force_is_given() {
        let cache = tempfile::tempdir().unwrap();
        let file = cache.path().join("scenes/a.axscene");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        std::fs::write(&file, [0; 10]).unwrap();

        for answer in ["", "n\n", "\n"] {
            let (result, said) = run_with(cache.path(), false, answer);
            assert!(result.is_ok());
            assert!(
                said.starts_with("Cache locations that will be cleared:\n"),
                "{said}"
            );
            assert!(
                said.ends_with("[y/N]: Cache clearing cancelled.\n"),
                "{said}"
            );
            assert!(file.exists(), "answer {answer:?} removed the file");
        }
        let (result, said) = run_with(cache.path(), false, "y\n");
        assert!(result.is_ok() && !file.exists());
        assert!(said.ends_with("Removed 1 file (10 B).\n"), "{said}");
        let (_, said) = run_with(cache.path(), true, "");
        assert_eq!(said, "No cached files found to clear.\n");
    }
}
