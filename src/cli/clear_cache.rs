//! `clear-cache`: lists the cache files, asks, removes them and says how much it removed.

use ax_devil::disk_cache;
use std::{
    io::{self, BufRead, Write},
    path::Path,
    process::ExitCode,
};

/// Clears the cache folder `cache_dir` (or reports why it is not known), asking on the standard input unless `force`.
pub fn run(cache_dir: Result<std::path::PathBuf, String>, force: bool) -> ExitCode {
    let result = cache_dir.and_then(|folder| {
        clear(
            &folder,
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
    force: bool,
    input: &mut impl BufRead,
    out: &mut impl Write,
) -> Result<(), String> {
    let disk_cache::Scan { locations, skipped } = disk_cache::scan(folder)?;
    for folder in &skipped {
        say(
            out,
            format!("{}: not a plain folder, skipped", folder.display()),
        )?;
    }
    if locations.is_empty() {
        return say(out, "No cached files found to clear.".into());
    }
    say(out, "Cache locations that will be cleared:".into())?;
    for location in &locations {
        say(
            out,
            format!(
                "{}: {} ({})",
                location.folder.display(),
                files(location.file_count()),
                size(location.bytes()),
            ),
        )?;
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
    let cleared = disk_cache::clear(&locations);
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
        let mut out = Vec::new();
        let result = clear(folder, force, &mut answer.as_bytes(), &mut out);
        (result, String::from_utf8(out).unwrap())
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
