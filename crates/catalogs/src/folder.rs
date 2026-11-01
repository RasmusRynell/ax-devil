//! The user's catalog folder, read one poll at a time. A file is read only once it looked the same on two polls in a
//! row, and a file counts as gone only once it was missing on two polls in a row, so an editor's half-written save or
//! its rename-then-write never reaches a lane.

use std::{
    collections::BTreeMap,
    fs::{self, Metadata},
    io::Read,
    path::{Path, PathBuf},
    sync::Arc,
    time::SystemTime,
};

use crate::{MAX_CATALOG_BYTES, MAX_USER_CATALOGS};

/// What a catalog file held when it was read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Content {
    /// The file's text, which the frontend's compiler judges.
    Text(Arc<str>),
    /// Why the file cannot be served.
    Problem(String),
}

/// What changed in the folder, by file name.
pub(crate) enum Change {
    /// The file was read and holds this now (it may hold what it did before).
    Set(String, Content),
    /// The file is gone.
    Remove(String),
}

/// What tells that a file was written: its size, modification time and identity.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Fingerprint {
    len: u64,
    modified: Option<SystemTime>,
    inode: u64,
}

impl Fingerprint {
    fn of(metadata: &Metadata) -> Self {
        Self {
            len: metadata.len(),
            modified: metadata.modified().ok(),
            inode: inode(metadata),
        }
    }
}

#[cfg(unix)]
fn inode(metadata: &Metadata) -> u64 {
    std::os::unix::fs::MetadataExt::ino(metadata)
}

#[cfg(not(unix))]
fn inode(_: &Metadata) -> u64 {
    0
}

/// What the polls saw of one file.
struct Tracked {
    /// The last poll's fingerprint.
    seen: Option<Fingerprint>,
    /// The fingerprint the last read was made under; `None` before a read and after one that may succeed on retry.
    read: Option<Fingerprint>,
    /// Whether the last poll found the file missing.
    missing: bool,
}

/// The poller's view of the folder.
pub(crate) struct Folder {
    path: PathBuf,
    tracked: BTreeMap<String, Tracked>,
    /// Whether the folder holds more catalog files than are listed, so the warning is given once.
    capped: bool,
}

impl Folder {
    pub(crate) fn new(path: PathBuf) -> Self {
        Self {
            path,
            tracked: BTreeMap::new(),
            capped: false,
        }
    }

    /// One poll. With `first`, every file is read now: nothing is being served yet that a half-written file could
    /// replace.
    pub(crate) fn poll(&mut self, first: bool) -> Vec<Change> {
        let Some(listed) = self.list() else {
            return Vec::new();
        };
        let mut changes = Vec::new();
        for (name, fingerprint) in &listed {
            let tracked = self.tracked.entry(name.clone()).or_insert(Tracked {
                seen: None,
                read: None,
                missing: false,
            });
            tracked.missing = false;
            let stable = first || tracked.seen == Some(*fingerprint);
            tracked.seen = Some(*fingerprint);
            if tracked.read != Some(*fingerprint) && stable {
                let (content, retry) = read(&self.path.join(name), fingerprint.len);
                tracked.read = (!retry).then_some(*fingerprint);
                changes.push(Change::Set(name.clone(), content));
            }
        }
        self.tracked.retain(|name, tracked| {
            if listed
                .binary_search_by(|(listed, _)| listed.cmp(name))
                .is_ok()
            {
                return true;
            }
            if tracked.missing {
                changes.push(Change::Remove(name.clone()));
                return false;
            }
            tracked.missing = true;
            true
        });
        changes
    }

    /// The catalog files in the folder by name, at most [`MAX_USER_CATALOGS`]; `None` when the folder cannot be read
    /// for a reason other than not existing, which leaves what is served as it is.
    fn list(&mut self) -> Option<Vec<(String, Fingerprint)>> {
        let mut found = Vec::new();
        match fs::read_dir(&self.path) {
            Ok(entries) => {
                for entry in entries.flatten() {
                    let Ok(name) = entry.file_name().into_string() else {
                        continue;
                    };
                    if !name.ends_with(".json") {
                        continue;
                    }
                    // `metadata` follows links, so a link to a file is a file and a dangling one is nothing.
                    if let Ok(metadata) = fs::metadata(entry.path())
                        && metadata.is_file()
                    {
                        found.push((name, Fingerprint::of(&metadata)));
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                tracing::debug!("Cannot list {}: {error}", self.path.display());
                return None;
            }
        }
        found.sort_by(|(a, _), (b, _)| a.cmp(b));
        let capped = found.len() > MAX_USER_CATALOGS;
        if capped && !self.capped {
            tracing::warn!(
                "{} holds {} catalog files; only the first {MAX_USER_CATALOGS} by name are listed",
                self.path.display(),
                found.len()
            );
        }
        self.capped = capped;
        found.truncate(MAX_USER_CATALOGS);
        Some(found)
    }
}

/// Reads the file; the flag says the failure may pass, so the next poll reads again.
fn read(path: &Path, len: u64) -> (Content, bool) {
    let too_large = |bytes: u64| {
        Content::Problem(format!(
            "The catalog is {bytes} bytes; at most {MAX_CATALOG_BYTES} are read"
        ))
    };
    if len > MAX_CATALOG_BYTES {
        return (too_large(len), false);
    }
    let mut bytes = Vec::new();
    let read = fs::File::open(path)
        .and_then(|file| file.take(MAX_CATALOG_BYTES + 1).read_to_end(&mut bytes));
    if let Err(error) = read {
        return (
            Content::Problem(format!("Cannot read the catalog: {error}")),
            true,
        );
    }
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return (too_large(bytes.len() as u64), false);
    }
    match String::from_utf8(bytes) {
        Ok(text) => (Content::Text(text.into()), false),
        Err(_) => (
            Content::Problem("The catalog is not UTF-8 text".into()),
            false,
        ),
    }
}
