//! The artifact file while it is being written, and where it ends up.

use std::{
    fs::File,
    io,
    path::{Path, PathBuf},
};

use tempfile::NamedTempFile;

use crate::io::create_private_dir;

/// The artifact file while it is being written.
pub(crate) enum Output {
    /// A temporary sibling of the final path; dropping it removes it.
    Named {
        temp: NamedTempFile,
        target: PathBuf,
    },
    Anonymous(File),
}

impl Output {
    /// A named temporary file beside `target`, or an unnamed one when there is no target or no way to write there.
    pub(crate) fn create(target: Option<&Path>) -> io::Result<Self> {
        if let Some(target) = target {
            match temporary_beside(target) {
                Ok(temp) => {
                    return Ok(Self::Named {
                        temp,
                        target: target.to_path_buf(),
                    });
                }
                Err(error) => {
                    tracing::warn!(cache = %target.display(), %error, "scene cache folder is not writable; keeping the artifact in a temporary file");
                }
            }
        }
        tempfile::tempfile().map(Self::Anonymous)
    }

    /// Another handle of the file, to write or read through.
    pub(crate) fn handle(&self) -> io::Result<File> {
        match self {
            Self::Named { temp, .. } => temp.as_file().try_clone(),
            Self::Anonymous(file) => file.try_clone(),
        }
    }

    /// The folder a named artifact lives in, where scratch files of the build go.
    pub(crate) fn folder(&self) -> Option<PathBuf> {
        match self {
            Self::Named { target, .. } => target.parent().map(Path::to_path_buf),
            Self::Anonymous(_) => None,
        }
    }

    /// The path the artifact is stored at once it is complete, if it has one.
    pub(crate) fn target(&self) -> Option<&Path> {
        match self {
            Self::Named { target, .. } => Some(target),
            Self::Anonymous(_) => None,
        }
    }

    /// Moves a named file into place and returns the handle to read it through.
    pub(crate) fn publish(self) -> File {
        match self {
            Self::Anonymous(file) => file,
            Self::Named { temp, target } => match temp.persist(&target) {
                Ok(file) => file,
                Err(error) => {
                    tracing::warn!(artifact = %target.display(), error = %error.error, "could not store the scene artifact; keeping it for this session only");
                    error.file.into_file()
                }
            },
        }
    }
}

/// A temporary file in the folder of `target`, private to the user.
pub(crate) fn temporary_beside(target: &Path) -> io::Result<NamedTempFile> {
    let Some(folder) = target.parent() else {
        return Err(io::Error::other("the artifact path has no folder"));
    };
    create_private_dir(folder)?;
    tempfile::Builder::new()
        .prefix(".axscene-")
        .suffix(".tmp")
        .tempfile_in(folder)
}
