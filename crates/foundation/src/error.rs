use crate::ItemId;
use std::{
    fmt,
    path::{Path, PathBuf},
};

/// Why something failed, as a public kind. A code and the ids in a [`Location::Item`] are all a
/// viewer who may not see the host's files is told; the reason and any path stay with the host.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ErrorCode {
    /// The path the user entered names no file.
    InvalidPath,
    /// The file does not exist.
    FileNotFound,
    /// The file or the path cannot be read.
    FileUnreadable,
    /// The file is readable but holds no video.
    NotAVideo,
    /// The video's codec, time base or picture size cannot be used.
    UnsupportedFormat,
    /// Building the frame index failed.
    IndexingFailed,
    /// The file changed after it was indexed or opened.
    FileChanged,
    /// A frame, or the decoder, failed.
    DecodeFailed,
    /// The Content is not in the workspace.
    ContentNotFound,
    /// A client message broke the playback protocol.
    ProtocolViolation,
    /// The video was closed while it was being opened or read.
    VideoClosed,
    /// A fault of the backend itself.
    Internal,
}

/// A path on the host's file system. It has no `Serialize` and no `Display`: showing one takes an explicit
/// [`as_path`](Self::as_path), so a path never reaches a wire or a message by accident.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct HostPath(PathBuf);

impl HostPath {
    /// A path on the host.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self(path.into())
    }

    /// The path itself, for use on the host and for a host-details audience.
    pub fn as_path(&self) -> &Path {
        &self.0
    }
}

/// What a failure happened to. Only the host sees a [`Host`](Self::Host) path; an item is named by its id, which is
/// safe to share.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Location {
    /// A file or folder on the host.
    Host(HostPath),
    /// A workspace item.
    Item(ItemId),
}

impl From<HostPath> for Location {
    fn from(path: HostPath) -> Self {
        Self::Host(path)
    }
}

impl From<ItemId> for Location {
    fn from(item: ItemId) -> Self {
        Self::Item(item)
    }
}

/// A failure the user can be shown: its public kind, what went wrong and what it happened to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UserError {
    /// The public kind, the only part every audience sees.
    pub code: ErrorCode,
    /// What went wrong, in words for the user. Free text that may name paths: for the host only.
    pub reason: String,
    /// What it happened to; `None` when it is about no particular file or item.
    pub location: Option<Location>,
}

impl UserError {
    /// An error of kind `code` with its reason and no location.
    pub fn new(code: ErrorCode, reason: impl Into<String>) -> Self {
        Self {
            code,
            reason: reason.into(),
            location: None,
        }
    }

    /// The same error, about `location`.
    #[must_use]
    pub fn at(mut self, location: impl Into<Location>) -> Self {
        self.location = Some(location.into());
        self
    }
}

impl fmt::Display for UserError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.reason)
    }
}

impl std::error::Error for UserError {}
