//! Small file helpers shared by the store.

use std::{
    fs::{self, File},
    io,
    path::Path,
};

use foundation::UserError;

/// Fills `buf` from `offset` without moving a shared file position, so a handle can serve several readers.
#[cfg(unix)]
pub(crate) fn read_exact_at(file: &File, buf: &mut [u8], offset: u64) -> io::Result<()> {
    std::os::unix::fs::FileExt::read_exact_at(file, buf, offset)
}

/// Fills `buf` from `offset` without relying on a shared file position.
#[cfg(windows)]
pub(crate) fn read_exact_at(file: &File, mut buf: &mut [u8], mut offset: u64) -> io::Result<()> {
    use std::os::windows::fs::FileExt;
    while !buf.is_empty() {
        match file.seek_read(buf, offset)? {
            0 => return Err(io::ErrorKind::UnexpectedEof.into()),
            read => {
                buf = &mut buf[read..];
                offset += read as u64;
            }
        }
    }
    Ok(())
}

/// Whether `path` names the file `open` is a handle of.
#[cfg(unix)]
pub(crate) fn names_file(path: &Path, open: &File) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (fs::metadata(path), open.metadata()) {
        (Ok(named), Ok(held)) => (named.dev(), named.ino()) == (held.dev(), held.ino()),
        _ => false,
    }
}

/// Without file identities nothing is claimed, so a caller never removes a file it cannot prove is its own.
#[cfg(not(unix))]
pub(crate) fn names_file(_path: &Path, _open: &File) -> bool {
    false
}

/// Creates `path` and its parents, readable by the user only where the platform has modes.
#[cfg(unix)]
pub(crate) fn create_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

/// Creates `path` and its parents.
#[cfg(not(unix))]
pub(crate) fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

/// An I/O failure as words for the user, located at `path`.
pub(crate) fn user_error(what: &str, error: &io::Error, path: &Path) -> UserError {
    UserError::new(format!("{what}: {error}"), path.display().to_string())
}
