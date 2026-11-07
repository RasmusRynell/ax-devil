//! Reading and atomically writing one workspace file.

use crate::document::{from_document, normalize, to_document};
use foundation::{ErrorCode, HostPath, UserError};
use serde_json::Value;
use std::{
    fs, io,
    io::Write,
    path::{Path, PathBuf},
};
use workspace::Workspace;

/// The workspace saved at `path`. A symlink at `path` is read at its target and relative paths anchor to the
/// target's folder.
pub fn load(path: &Path) -> Result<Workspace, UserError> {
    read_file(path).map(|(workspace, _)| workspace)
}

/// `workspace` as the document a file holds, with every path absolute, for handing it to another process.
pub fn to_json(workspace: &Workspace) -> Value {
    to_document(workspace, None, None)
}

/// The workspace in a document [`to_json`] made.
pub fn from_json(document: &Value) -> Result<Workspace, String> {
    let bytes = serde_json::to_vec(document).map_err(|error| error.to_string())?;
    from_document(&bytes, "the hand-off", Path::new("")).map(|(workspace, _)| workspace)
}

/// Writes `workspace` to `path`, replacing it only when complete. A symlink at `path` stays a link and its target is
/// written; an existing file keeps its permissions.
pub fn save(workspace: &Workspace, path: &Path) -> Result<(), UserError> {
    let fail = |error: &dyn std::fmt::Display| {
        UserError::new(
            ErrorCode::SaveFailed,
            format!("Could not save {}: {error}", file_name(path)),
        )
        .at(HostPath::new(path))
    };
    let backing = backing(path).map_err(|error| fail(&error))?;
    let document = to_document(workspace, normalize(&backing).parent(), None);
    write_file(&backing, &document).map_err(|error| fail(&error))
}

/// Writes `text` to `path` like [`save`]: replacing the file only when complete, and leaving no temporary file when it
/// cannot be written. The error names the file.
pub fn write_text(path: &Path, text: &str) -> Result<(), UserError> {
    let fail = |error: &dyn std::fmt::Display| {
        UserError::new(
            ErrorCode::SaveFailed,
            format!("Could not write {}: {error}", path.display()),
        )
        .at(HostPath::new(path))
    };
    let backing = backing(path).map_err(|error| fail(&error))?;
    write_bytes(&backing, text.as_bytes()).map_err(|error| fail(&error))
}

/// The file `path` stands for: its target when it is a symlink.
fn backing(path: &Path) -> io::Result<PathBuf> {
    if fs::symlink_metadata(path).is_ok_and(|meta| meta.is_symlink()) {
        fs::canonicalize(path)
    } else {
        std::path::absolute(path)
    }
}

pub(crate) fn file_name(path: &Path) -> String {
    path.file_name()
        .map_or_else(String::new, |name| name.to_string_lossy().into_owned())
}

/// Reads the document at `path`: the workspace and, in a recovery copy, the file it recovers.
pub(crate) fn read_file(path: &Path) -> Result<(Workspace, Option<PathBuf>), UserError> {
    let name = file_name(path);
    let at = |code, reason| UserError::new(code, reason).at(HostPath::new(path));
    let unreadable = |error: io::Error| match error.kind() {
        io::ErrorKind::NotFound => at(ErrorCode::FileNotFound, format!("{name} was not found.")),
        _ => at(
            ErrorCode::FileUnreadable,
            format!("Cannot read {name}: {error}"),
        ),
    };
    let backing = backing(path).map_err(unreadable)?;
    let bytes = fs::read(&backing).map_err(unreadable)?;
    let base = normalize(&backing);
    from_document(&bytes, &name, base.parent().unwrap_or(&base))
        .map_err(|reason| at(ErrorCode::WorkspaceInvalid, reason))
}

/// Writes `document` to the file `backing` as pretty JSON, as [`write_bytes`] does.
pub(crate) fn write_file(backing: &Path, document: &Value) -> io::Result<()> {
    let mut text = serde_json::to_string_pretty(document)?;
    text.push('\n');
    write_bytes(backing, text.as_bytes())
}

/// Writes `bytes` to the file `backing` through a temporary file in its folder, so a failure keeps the old file.
/// A new file is 0600 on Unix; an existing one keeps its permissions. Creates no folders.
fn write_bytes(backing: &Path, bytes: &[u8]) -> io::Result<()> {
    let folder = backing
        .parent()
        .filter(|folder| !folder.as_os_str().is_empty());
    let mut temp = tempfile::NamedTempFile::new_in(folder.unwrap_or(Path::new(".")))?;
    if let Ok(existing) = fs::metadata(backing) {
        temp.as_file().set_permissions(existing.permissions())?;
    }
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    temp.persist(backing).map_err(|error| error.error)?;
    Ok(())
}
