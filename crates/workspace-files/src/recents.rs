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

    /// The entries, newest first. Never touches the files they name. A missing, unreadable or malformed file is an
    /// empty list (with a warning unless missing); malformed entries are skipped.
    pub fn list(&self) -> Vec<Recent> {
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
                })
            })
            .collect()
    }

    /// Puts `path` first, replacing its earlier entry, and keeps the newest 8.
    pub fn record(&self, path: &Path, now_ms: u64) -> io::Result<()> {
        let path = normalize(path);
        self.update(|list| {
            list.retain(|recent| recent.path != path);
            list.insert(
                0,
                Recent {
                    path,
                    used_ms: now_ms,
                },
            );
            list.truncate(MOST);
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
        let mut list = self.list();
        change(&mut list);
        let entries: Vec<Value> = list
            .iter()
            .map(|recent| json!({"path": recent.path.to_string_lossy(), "used_ms": recent.used_ms}))
            .collect();
        write_file(&self.file, &Value::Array(entries))
    }
}
