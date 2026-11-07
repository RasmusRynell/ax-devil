//! The user's catalog folder, read one poll at a time. A file is read only once it looked the same on two polls in a
//! row, and a file counts as gone only once it was missing on two polls in a row, so an editor's half-written save or
//! its rename-then-write never reaches a lane. Only regular files are read, opened without following links and
//! without blocking, and checked on the opened handle: a link, a pipe or a device inside the folder is reported, never
//! followed, so nothing outside the folder is served and nothing can stall the poll.

use std::{
    collections::BTreeMap,
    fs::{self, File, Metadata},
    io::{self, Read as _},
    path::{Path, PathBuf},
    sync::Arc,
    time::SystemTime,
};

use crate::{MAX_CATALOG_BYTES, MAX_USER_CATALOGS, id::is_file_name};

/// Why an entry that is not a regular file is listed but never read.
const NOT_REGULAR: &str = "The catalog is not a regular file (links and special files are ignored)";
/// Why a file whose name is no catalog id is listed but never read.
pub(crate) const ODD_NAME: &str = "The file name has a control character, a backslash or bytes that are not UTF-8; rename the file";

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

/// A `.json` entry of the folder as a poll listed it.
struct Found {
    fingerprint: Fingerprint,
    /// Set when the entry is not to be read, and why.
    refusal: Option<&'static str>,
}

/// What reading one file came to.
enum Read {
    /// The file was read: this is what it holds.
    Done(Content),
    /// The file could not be read; the problem is served until a later poll reads it.
    Failed(Content),
    /// The file changed after the poll listed it, so nothing is served from this read and the next poll reads again.
    Changed,
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

    /// The folder's path.
    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    /// Starts tracking `name`, a file the app just wrote: the polls read it like any file, so a copy removed before
    /// they did is still noticed.
    pub(crate) fn adopt(&mut self, name: &str) {
        self.tracked.insert(
            name.to_owned(),
            Tracked {
                seen: None,
                read: None,
                missing: false,
            },
        );
    }

    /// One poll. With `first`, every file is read now: nothing is being served yet that a half-written file could
    /// replace.
    pub(crate) fn poll(&mut self, first: bool) -> Vec<Change> {
        let Some(listed) = self.list() else {
            return Vec::new();
        };
        let mut changes = Vec::new();
        for (name, found) in &listed {
            let fingerprint = &found.fingerprint;
            let tracked = self.tracked.entry(name.clone()).or_insert(Tracked {
                seen: None,
                read: None,
                missing: false,
            });
            tracked.missing = false;
            let stable = first || tracked.seen == Some(*fingerprint);
            tracked.seen = Some(*fingerprint);
            if tracked.read != Some(*fingerprint) && stable {
                let content = match found.refusal {
                    Some(refusal) => Read::Done(Content::Problem(refusal.into())),
                    None => read(&self.path.join(name), fingerprint),
                };
                match content {
                    Read::Done(content) => {
                        tracked.read = Some(*fingerprint);
                        changes.push(Change::Set(name.clone(), content));
                    }
                    Read::Failed(content) => {
                        tracked.read = None;
                        changes.push(Change::Set(name.clone(), content));
                    }
                    Read::Changed => tracked.read = None,
                }
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
    fn list(&mut self) -> Option<Vec<(String, Found)>> {
        let mut found = BTreeMap::new();
        match fs::read_dir(&self.path) {
            Ok(entries) => {
                for entry in entries.flatten() {
                    let file_name = entry.file_name();
                    let lossy = file_name.to_string_lossy();
                    if !lossy.ends_with(".json") {
                        continue;
                    }
                    // `DirEntry` reports the entry itself, not what a link points at.
                    let (Ok(kind), Ok(metadata)) = (entry.file_type(), entry.metadata()) else {
                        continue;
                    };
                    if kind.is_dir() {
                        continue;
                    }
                    let fingerprint = Fingerprint::of(&metadata);
                    let refusal = (!kind.is_file()).then_some(NOT_REGULAR);
                    match file_name.to_str().filter(|name| is_file_name(name)) {
                        Some(name) => {
                            found.insert(
                                name.to_owned(),
                                Found {
                                    fingerprint,
                                    refusal,
                                },
                            );
                        }
                        None => {
                            // Listed under a name made safe, which no file has, unless one does: that one wins.
                            let name = lossy
                                .chars()
                                .map(|c| {
                                    if c.is_control() || c == '\\' {
                                        '\u{fffd}'
                                    } else {
                                        c
                                    }
                                })
                                .collect();
                            found.entry(name).or_insert(Found {
                                fingerprint,
                                refusal: Some(ODD_NAME),
                            });
                        }
                    }
                }
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                tracing::debug!("Cannot list {}: {error}", self.path.display());
                return None;
            }
        }
        let capped = found.len() > MAX_USER_CATALOGS;
        if capped && !self.capped {
            tracing::warn!(
                "{} holds {} catalog files; only the first {MAX_USER_CATALOGS} by name are listed",
                self.path.display(),
                found.len()
            );
        }
        self.capped = capped;
        Some(found.into_iter().take(MAX_USER_CATALOGS).collect())
    }
}

/// Opens `path` for reading without following a link at the end of it and, on Unix, without waiting for a pipe or a
/// device to have something to give.
fn open(path: &Path) -> io::Result<File> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::custom_flags(
        &mut options,
        libc::O_NOFOLLOW | libc::O_NONBLOCK,
    );
    options.open(path)
}

/// Reads the file the poll listed as `seen`. Everything is judged on the opened handle, so a file replaced after the
/// listing by a link, a pipe or other content is never served; the name has no separator, so the open stays inside
/// the folder.
fn read(path: &Path, seen: &Fingerprint) -> Read {
    let failed = |error: io::Error| {
        Read::Failed(Content::Problem(format!(
            "Cannot read the catalog: {error}"
        )))
    };
    let too_large = |bytes: u64| {
        Read::Done(Content::Problem(format!(
            "The catalog is {bytes} bytes; at most {MAX_CATALOG_BYTES} are read"
        )))
    };
    let file = match open(path) {
        Ok(file) => file,
        Err(error) => return failed(error),
    };
    let opened = match file.metadata() {
        Ok(opened) => opened,
        Err(error) => return failed(error),
    };
    if !opened.is_file() {
        return Read::Done(Content::Problem(NOT_REGULAR.into()));
    }
    if Fingerprint::of(&opened) != *seen {
        return Read::Changed;
    }
    if opened.len() > MAX_CATALOG_BYTES {
        return too_large(opened.len());
    }
    let mut bytes = Vec::new();
    if let Err(error) = (&file).take(MAX_CATALOG_BYTES + 1).read_to_end(&mut bytes) {
        return failed(error);
    }
    match file.metadata() {
        Ok(after) if Fingerprint::of(&after) == *seen => {}
        Ok(_) => return Read::Changed,
        Err(error) => return failed(error),
    }
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return too_large(bytes.len() as u64);
    }
    Read::Done(match String::from_utf8(bytes) {
        Ok(text) => Content::Text(text.into()),
        Err(_) => Content::Problem("The catalog is not UTF-8 text".into()),
    })
}

#[cfg(all(test, unix))]
mod tests;
