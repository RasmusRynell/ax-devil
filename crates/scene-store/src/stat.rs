//! What the file system says about a source file, taken from the open handle.

use std::{fs::File, io};

use serde::{Deserialize, Serialize};

/// Size and times of a file at one moment.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct Stat {
    pub size: u64,
    pub mtime_ns: i64,
    /// The time the file or its metadata last changed, on platforms that keep one. Without it a matching stat proves
    /// nothing, because an edit can restore the modification time.
    pub ctime_ns: Option<i64>,
}

impl Stat {
    /// The stat of the file behind `file`, so what is measured is the file that is then read.
    pub(crate) fn of(file: &File) -> io::Result<Self> {
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(io::Error::other("it is not a regular file"));
        }
        Ok(Self::from_metadata(&metadata))
    }

    /// Whether a matching stat is enough to know the content is unchanged.
    pub(crate) fn proves_unchanged(&self, stored: &Stat) -> bool {
        self.ctime_ns.is_some() && self == stored
    }

    #[cfg(unix)]
    fn from_metadata(metadata: &std::fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;
        let nanos = |secs: i64, nsec: i64| secs.saturating_mul(1_000_000_000).saturating_add(nsec);
        Self {
            size: metadata.len(),
            mtime_ns: nanos(metadata.mtime(), metadata.mtime_nsec()),
            ctime_ns: Some(nanos(metadata.ctime(), metadata.ctime_nsec())),
        }
    }

    #[cfg(not(unix))]
    fn from_metadata(metadata: &std::fs::Metadata) -> Self {
        let mtime_ns = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |since| {
                i64::try_from(since.as_nanos()).unwrap_or(i64::MAX)
            });
        Self {
            size: metadata.len(),
            mtime_ns,
            ctime_ns: None,
        }
    }
}
