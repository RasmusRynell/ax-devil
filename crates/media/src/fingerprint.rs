//! A cheap identity of a file's contents: its stat values plus a digest of its first and last 64 KiB. Hashing a
//! multi-gigabyte video in full would defeat instant reopening, so edits in the middle that keep size, times and
//! both ends are not noticed; that is the accepted trade-off.

use crate::input::VideoInput;
use crate::sequence::Numbering;
use std::fs::{File, Metadata};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;
use xxhash_rust::xxh3::Xxh3;

const SAMPLE: u64 = 64 * 1024;

/// Identity of a file at one moment.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Fingerprint {
    pub(crate) device: u64,
    pub(crate) inode: u64,
    pub(crate) size: u64,
    pub(crate) mtime_ns: i64,
    pub(crate) ctime_ns: i64,
    /// xxh3 of the first and the last 64 KiB; a file under 128 KiB is hashed whole.
    pub(crate) sample: u64,
}

impl Fingerprint {
    /// Fingerprints the file behind an open handle, so what is measured is the file that is then read.
    pub(crate) fn of_handle(file: &File) -> io::Result<Self> {
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(io::Error::other("it is not a regular file"));
        }
        let size = metadata.len();
        let head = size.min(SAMPLE);
        let tail = size.saturating_sub(head).min(SAMPLE);
        let mut hash = Xxh3::new();
        let mut reader = file;
        reader.seek(SeekFrom::Start(0))?;
        feed(&mut hash, reader, head)?;
        reader.seek(SeekFrom::Start(size - tail))?;
        feed(&mut hash, reader, tail)?;
        Ok(Self::from_metadata(&metadata, hash.digest()))
    }

    /// Fingerprints the file currently at `path`.
    pub(crate) fn of_path(path: &Path) -> io::Result<Self> {
        Self::of_handle(&File::open(path)?)
    }

    /// Fingerprints what identifies `input` right now: its file, or for an image sequence its first image. The
    /// error is a reason for the user.
    pub(crate) fn of_input(input: &VideoInput) -> Result<Self, String> {
        let path = match input {
            VideoInput::File(path) => path.clone(),
            VideoInput::ImageSequence(sequence) => {
                Numbering::parse(&sequence.pattern)?.path(u64::from(sequence.start_number))
            }
        };
        Self::of_path(&path).map_err(|e| format!("{} cannot be read: {e}", path.display()))
    }

    /// One number that is equal while the fingerprint is.
    pub(crate) fn digest(&self) -> u64 {
        let mut hash = Xxh3::new();
        hash.update(&self.device.to_le_bytes());
        hash.update(&self.inode.to_le_bytes());
        hash.update(&self.size.to_le_bytes());
        hash.update(&self.mtime_ns.to_le_bytes());
        hash.update(&self.ctime_ns.to_le_bytes());
        hash.update(&self.sample.to_le_bytes());
        hash.digest()
    }

    #[cfg(unix)]
    fn from_metadata(metadata: &Metadata, sample: u64) -> Self {
        use std::os::unix::fs::MetadataExt;
        let nanos = |secs: i64, nsec: i64| secs.saturating_mul(1_000_000_000).saturating_add(nsec);
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            size: metadata.len(),
            mtime_ns: nanos(metadata.mtime(), metadata.mtime_nsec()),
            ctime_ns: nanos(metadata.ctime(), metadata.ctime_nsec()),
            sample,
        }
    }

    #[cfg(not(unix))]
    fn from_metadata(metadata: &Metadata, sample: u64) -> Self {
        let mtime_ns = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |since| {
                i64::try_from(since.as_nanos()).unwrap_or(i64::MAX)
            });
        Self {
            device: 0,
            inode: 0,
            size: metadata.len(),
            mtime_ns,
            ctime_ns: 0,
            sample,
        }
    }
}

/// Hashes the next `count` bytes of `reader`; fewer when the file shrank while reading, which the next
/// fingerprint then shows.
fn feed(hash: &mut Xxh3, reader: impl Read, count: u64) -> io::Result<()> {
    let mut bytes = Vec::new();
    reader.take(count).read_to_end(&mut bytes)?;
    hash.update(&bytes);
    Ok(())
}
