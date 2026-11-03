//! The recent workspaces list: a small lenient JSON file shared by every window and backend of the user.

use crate::{disk::write_file, document::normalize};
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io,
    path::{Path, PathBuf},
};

const MOST: usize = 8;

/// A workspace file opened or saved lately.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recent {
    /// The file, absolute and lexically normalized; it may no longer exist.
    pub path: PathBuf,
    /// When it was last used, in Unix milliseconds.
    pub used_ms: u64,
    /// For a recovery copy of a saved file, that file; the copy then stands for it in [`Recents::list`].
    pub recovers: Option<PathBuf>,
}

/// The recent workspaces list in one file. Callers run it off the async executor.
pub struct Recents {
    file: PathBuf,
}

impl Recents {
    /// The list kept in `file`, made absolute.
    pub fn new(file: PathBuf) -> Self {
        Self {
            file: normalize(&file),
        }
    }

    /// The workspaces, newest first: a saved file with a recovery copy in the list is left out, since the copy is it.
    /// Never touches the files they name.
    pub fn list(&self) -> Vec<Recent> {
        let mut list = self.stored();
        let mut hidden = hidden(&list).into_iter();
        list.retain(|_| !hidden.next().unwrap_or(false));
        list
    }

    /// Every entry, newest first. A missing, unreadable or malformed file is an empty list (with a warning unless
    /// missing); malformed entries are skipped.
    fn stored(&self) -> Vec<Recent> {
        let bytes = match fs::read(&self.file) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Vec::new(),
            Err(error) => {
                tracing::warn!(
                    "Recent workspaces {} cannot be read: {error}",
                    self.file.display()
                );
                return Vec::new();
            }
        };
        let Ok(Value::Array(entries)) = serde_json::from_slice(&bytes) else {
            tracing::warn!(
                "Recent workspaces {} is not a JSON list",
                self.file.display()
            );
            return Vec::new();
        };
        entries
            .iter()
            .filter_map(|entry| {
                Some(Recent {
                    path: PathBuf::from(entry.get("path")?.as_str()?),
                    used_ms: entry.get("used_ms")?.as_u64()?,
                    recovers: entry
                        .get("recovers")
                        .and_then(Value::as_str)
                        .map(PathBuf::from),
                })
            })
            .collect()
    }

    /// Puts `path` first, replacing its earlier entry, and keeps the newest 8 workspaces.
    pub fn record(&self, path: &Path, now_ms: u64) -> io::Result<()> {
        self.record_copy(path, None, now_ms)
    }

    /// Like [`record`](Self::record) for a recovery copy; `recovers` is the saved file it holds changes to.
    pub fn record_copy(&self, path: &Path, recovers: Option<&Path>, now_ms: u64) -> io::Result<()> {
        let (path, recovers) = (normalize(path), recovers.map(normalize));
        self.update(|list| {
            list.retain(|recent| recent.path != path);
            list.insert(
                0,
                Recent {
                    path,
                    used_ms: now_ms,
                    recovers,
                },
            );
            // Counted after the hiding, so what is listed is as many distinct workspaces as there is room for.
            let mut hidden = hidden(list).into_iter();
            let mut shown = 0;
            list.retain(|_| {
                shown += usize::from(!hidden.next().unwrap_or(false));
                shown <= MOST
            });
        })
    }

    /// Removes `path` from the list.
    pub fn forget(&self, path: &Path) -> io::Result<()> {
        let path = normalize(path);
        self.update(|list| list.retain(|recent| recent.path != path))
    }

    /// Applies `change` to the list under a lock on `<file>.lock`, so concurrent changes of any process all land.
    fn update(&self, change: impl FnOnce(&mut Vec<Recent>)) -> io::Result<()> {
        if let Some(folder) = self.file.parent() {
            fs::create_dir_all(folder)?;
        }
        let mut lock = self.file.as_os_str().to_owned();
        lock.push(".lock");
        let lock = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(lock)?;
        lock.lock()?;
        let mut list = self.stored();
        change(&mut list);
        let entries: Vec<Value> = list
            .iter()
            .map(|recent| {
                let mut entry =
                    json!({"path": recent.path.to_string_lossy(), "used_ms": recent.used_ms});
                if let Some(recovers) = &recent.recovers {
                    entry["recovers"] = recovers.to_string_lossy().into();
                }
                entry
            })
            .collect();
        write_file(&self.file, &Value::Array(entries))
    }
}

/// Which entries are saved files whose recovery copy is listed before them, so newer: a file used after its copy was
/// written is the latest thing the user did. The list's order says which came last, however close the times.
/// Compared as spelled, so nothing is touched.
fn hidden(list: &[Recent]) -> Vec<bool> {
    (0..list.len())
        .map(|at| {
            let file = &list[at];
            file.recovers.is_none()
                && list[..at]
                    .iter()
                    .any(|copy| copy.recovers.as_ref() == Some(&file.path))
        })
        .collect()
}
