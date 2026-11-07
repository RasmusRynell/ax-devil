//! The settings file on disk: reading with the version check, the backup before a migration, atomic writes.

use crate::Error;
use serde::Serialize;
use serde_json::{Map, Value};
use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// An error about the file as a whole, which is not the user's input at fault.
pub(crate) fn fail(path: &Path, reason: impl Into<String>) -> Error {
    Error {
        location: path.display().to_string(),
        reason: reason.into(),
        invalid: false,
    }
}

/// A file that was read and parsed.
pub(crate) struct Read {
    /// The format version the file says it has.
    pub version: u64,
    /// Everything in the file except `version`.
    pub document: Map<String, Value>,
    /// The file as it was on disk.
    pub bytes: Vec<u8>,
}

/// Reads the file; `None` when it does not exist. Refuses anything that is not a JSON object with an integer
/// `version` from 1 to `current`.
pub(crate) fn read(path: &Path, current: u64) -> Result<Option<Read>, Error> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(fail(path, error.to_string())),
    };
    let Value::Object(mut document) =
        serde_json::from_slice(&bytes).map_err(|error| fail(path, error.to_string()))?
    else {
        return Err(fail(path, "not a JSON object"));
    };
    let version = document
        .remove("version")
        .and_then(|version| version.as_u64())
        .filter(|&version| version >= 1);
    let Some(version) = version else {
        return Err(fail(
            path,
            "\"version\" is missing or is not a whole number from 1",
        ));
    };
    if version > current {
        return Err(fail(
            path,
            format!("written by a newer ax-devil (v{version}; this one reads up to v{current})"),
        ));
    }
    Ok(Some(Read {
        version,
        document,
        bytes,
    }))
}

/// Keeps `bytes`, the file as it was at `version`, next to `path` as `<name>.v<version>.json`. An existing backup is
/// never overwritten: one with the same bytes is reused, a different one makes this copy `<name>.v<version>.<n>.json`.
pub(crate) fn backup(path: &Path, version: u64, bytes: &[u8]) -> Result<PathBuf, Error> {
    let stem = path
        .file_stem()
        .map_or_else(|| "settings".into(), |stem| stem.to_string_lossy());
    for attempt in 0_u32.. {
        let name = match attempt {
            0 => format!("{stem}.v{version}.json"),
            n => format!("{stem}.v{version}.{n}.json"),
        };
        let target = path.with_file_name(name);
        match write_new(&target, bytes) {
            Ok(()) => return Ok(target),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                if fs::read(&target).is_ok_and(|existing| existing == bytes) {
                    return Ok(target);
                }
            }
            Err(error) => return Err(fail(&target, error.to_string())),
        }
    }
    unreachable!("the attempts are unbounded")
}

/// Writes `version` first, then the rest of `document`.
pub(crate) fn write(path: &Path, version: u64, document: &Map<String, Value>) -> Result<(), Error> {
    #[derive(Serialize)]
    struct File<'a> {
        version: u64,
        #[serde(flatten)]
        rest: &'a Map<String, Value>,
    }
    let mut text = serde_json::to_string_pretty(&File {
        version,
        rest: document,
    })
    .map_err(|error| fail(path, error.to_string()))?;
    text.push('\n');
    write_atomically(path, text.as_bytes()).map_err(|error| fail(path, error.to_string()))
}

/// Writes `bytes` to a new, uniquely named file next to `path` (created exclusively, so it never follows an
/// existing link) and renames it into place, replacing `path` (or the link at `path`, never its target), so a crash
/// never leaves half a file. A missing folder is created readable by the user only, because the file may hold
/// credentials.
pub(crate) fn write_atomically(path: &Path, bytes: &[u8]) -> io::Result<()> {
    stage(path, bytes)?
        .persist(path)
        .map_err(|error| error.error)?;
    Ok(())
}

/// Like `write_atomically`, but fails with `AlreadyExists` instead of replacing anything at `path`.
fn write_new(path: &Path, bytes: &[u8]) -> io::Result<()> {
    stage(path, bytes)?
        .persist_noclobber(path)
        .map_err(|error| error.error)?;
    Ok(())
}

fn stage(path: &Path, bytes: &[u8]) -> io::Result<tempfile::NamedTempFile> {
    let folder = path.parent().unwrap_or_else(|| Path::new("."));
    let mut folders = fs::DirBuilder::new();
    folders.recursive(true);
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut folders, 0o700);
    folders.create(folder)?;
    let mut file = tempfile::NamedTempFile::new_in(folder)?;
    io::Write::write_all(&mut file, bytes)?;
    file.as_file().sync_all()?;
    Ok(file)
}
