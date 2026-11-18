//! The file an output is written to before it replaces its destination.
//!
//! The file is created exclusively beside the destination (an existing name or symlink is never reused or followed)
//! and stays open until it is renamed into place, so what is written is the file that was created. A party that
//! controls the destination's folder can still swap the path, so FFmpeg is given a name that cannot be swapped where
//! the platform has one (`/proc/self/fd/N` on Linux), and the path is checked to still be that file before it is
//! handed out and before the rename. On other platforms the time between the last check and FFmpeg's own open, and
//! the one between the check and the rename, remain; a swap there can only truncate what the swapped path names, or
//! publish a different file at the destination, which that party could do to the destination anyway.

use foundation::{ErrorCode, HostPath, UserError};
use std::{
    fs::{File, Metadata, OpenOptions},
    io::{ErrorKind, Write},
    path::{Path, PathBuf},
};

/// A new empty file beside a destination. Removed on drop unless [`persist`] moved it.
///
/// [`persist`]: PartFile::persist
pub(crate) struct PartFile {
    /// Open until the rename.
    file: Option<File>,
    path: PathBuf,
    kept: bool,
}

impl PartFile {
    /// Creates `.<name>.<process>-<n>.part` beside `destination`, failing rather than reusing a name.
    pub(crate) fn create(destination: &Path) -> Result<Self, UserError> {
        let (Some(parent), Some(name)) = (destination.parent(), destination.file_name()) else {
            return Err(UserError::new(
                ErrorCode::InvalidPath,
                "The output must be a file path",
            ));
        };
        let name = name.to_string_lossy();
        for attempt in 0..100 {
            let path = parent.join(format!(".{name}.{}-{attempt}.part", std::process::id()));
            // `create_new` is O_EXCL: it fails on a symlink at `path` instead of following it.
            match OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .open(&path)
            {
                Ok(file) => {
                    return Ok(Self {
                        file: Some(file),
                        path,
                        kept: false,
                    });
                }
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
                Err(error) => {
                    return Err(UserError::new(
                        ErrorCode::FileUnreadable,
                        format!("The output cannot be created: {error}"),
                    )
                    .at(HostPath::new(destination)));
                }
            }
        }
        Err(UserError::new(
            ErrorCode::Internal,
            "no free name for the temporary output file",
        ))
    }

    /// The path FFmpeg writes the file through, after checking that the path still names the file.
    pub(crate) fn writable_path(&self) -> Result<PathBuf, UserError> {
        self.verify()?;
        Ok(self.by_descriptor().unwrap_or_else(|| self.path.clone()))
    }

    /// Writes `bytes` as the whole file.
    pub(crate) fn write_all(&mut self, bytes: &[u8]) -> Result<(), UserError> {
        self.verify()?;
        let file = self.file.as_mut().ok_or_else(Self::gone)?;
        file.write_all(bytes).map_err(|error| {
            UserError::new(
                ErrorCode::FileUnreadable,
                format!("The output cannot be written: {error}"),
            )
        })
    }

    /// Moves the file over `destination`.
    pub(crate) fn persist(mut self, destination: &Path) -> Result<(), UserError> {
        self.verify()?;
        self.file = None;
        std::fs::rename(&self.path, destination).map_err(|error| {
            UserError::new(
                ErrorCode::FileUnreadable,
                format!("The output cannot be put in place: {error}"),
            )
            .at(HostPath::new(destination))
        })?;
        self.kept = true;
        Ok(())
    }

    /// Fails unless the path still names the file that was created: not a symlink, not another file.
    fn verify(&self) -> Result<(), UserError> {
        let file = self.file.as_ref().ok_or_else(Self::gone)?;
        let same = match (file.metadata(), std::fs::symlink_metadata(&self.path)) {
            (Ok(ours), Ok(found)) => found.is_file() && same_file(&ours, &found),
            _ => false,
        };
        if same {
            Ok(())
        } else {
            Err(UserError::new(
                ErrorCode::FileUnreadable,
                "The temporary output file was replaced while it was being written",
            )
            .at(HostPath::new(&self.path)))
        }
    }

    fn gone() -> UserError {
        UserError::new(ErrorCode::Internal, "the temporary output file is closed")
    }

    /// A name for the open file that no change to the folder can redirect.
    #[cfg(target_os = "linux")]
    fn by_descriptor(&self) -> Option<PathBuf> {
        use std::os::fd::AsRawFd;
        let path = PathBuf::from(format!("/proc/self/fd/{}", self.file.as_ref()?.as_raw_fd()));
        // Absent when /proc is not mounted.
        path.exists().then_some(path)
    }

    #[cfg(not(target_os = "linux"))]
    fn by_descriptor(&self) -> Option<PathBuf> {
        None
    }
}

impl Drop for PartFile {
    fn drop(&mut self) {
        if !self.kept {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

#[cfg(unix)]
fn same_file(a: &Metadata, b: &Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    (a.dev(), a.ino()) == (b.dev(), b.ino())
}

#[cfg(not(unix))]
fn same_file(_: &Metadata, _: &Metadata) -> bool {
    true
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn a_swapped_path_is_never_written_through_or_put_in_place() {
        let folder = tempfile::tempdir().expect("a temporary folder");
        let victim = folder.path().join("victim.txt");
        std::fs::write(&victim, "keep me").unwrap();
        let destination = folder.path().join("out.png");

        let mut part = PartFile::create(&destination).expect("a part file");
        std::fs::remove_file(&part.path).unwrap();
        std::os::unix::fs::symlink(&victim, &part.path).unwrap();

        assert!(part.writable_path().is_err());
        assert!(part.write_all(b"x").is_err());
        assert!(part.persist(&destination).is_err());
        assert_eq!(std::fs::read_to_string(&victim).unwrap(), "keep me");
        assert!(!destination.exists());
    }
}
