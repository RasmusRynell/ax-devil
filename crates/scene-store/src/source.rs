//! The overlay file as the host holds it: read once, lazily, through the handle that was fingerprinted.

use std::{
    fs::File,
    io::{self, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::{
        OnceLock,
        atomic::{AtomicBool, Ordering},
    },
};

use foundation::{CancelToken, Interrupt, UserError};
use handler_api::DecodeError;
use sha2::{Digest, Sha256};

use crate::{io::user_error, stat::Stat};

/// Bytes read from a source between cancellation checks.
const CHUNK: usize = 4 << 20;

/// A source file whose bytes are read on the first request. A decoder that takes the path (a plugin) never asks,
/// so a file of any size is never held in memory for it.
pub(crate) struct LazySource {
    path: PathBuf,
    file: File,
    stat: Stat,
    bytes: OnceLock<Vec<u8>>,
    digest: OnceLock<[u8; 32]>,
}

impl LazySource {
    /// `file` is the handle `stat` was taken from.
    pub(crate) fn new(path: PathBuf, file: File, stat: Stat) -> Self {
        Self {
            path,
            file,
            stat,
            bytes: OnceLock::new(),
            digest: OnceLock::new(),
        }
    }

    pub(crate) fn path_buf(&self) -> PathBuf {
        self.path.clone()
    }

    /// SHA-256 of the whole file; reads it only when the decoder did not.
    pub(crate) fn digest(&self, cancel: &CancelToken) -> Result<[u8; 32], Interrupt<UserError>> {
        if let Some(digest) = self.digest.get() {
            return Ok(*digest);
        }
        read_through(&self.file, &self.path, None, cancel)
    }

    /// Whether the file still has the stat it was opened with.
    pub(crate) fn unchanged(&self) -> bool {
        Stat::of(&self.file).is_ok_and(|now| now == self.stat)
    }

    /// Where the file is.
    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    /// The whole file, read once through the handle that was fingerprinted; later calls return the same bytes.
    pub(crate) fn bytes(&self, cancel: &CancelToken) -> Result<&[u8], Interrupt<DecodeError>> {
        cancel.check()?;
        if let Some(bytes) = self.bytes.get() {
            return Ok(bytes);
        }
        let mut bytes = Vec::with_capacity(usize::try_from(self.stat.size).unwrap_or(0));
        let digest = match read_through(&self.file, &self.path, Some(&mut bytes), cancel) {
            Ok(digest) => digest,
            Err(Interrupt::Cancelled) => return Err(Interrupt::Cancelled),
            Err(Interrupt::Failed(error)) => {
                return Err(Interrupt::Failed(DecodeError(error.reason)));
            }
        };
        let _ = self.digest.set(digest);
        Ok(self.bytes.get_or_init(|| bytes))
    }
}

/// A track's own handle on its overlay file, kept to notice that the file was written in place. The artifact is a
/// private snapshot, so reading it never shows an edit; this is what stops a track from serving Scenes of a file that
/// is no longer the one it describes.
pub(crate) struct SourceWatch {
    path: PathBuf,
    file: File,
    stat: Stat,
    rewritten: AtomicBool,
}

impl SourceWatch {
    /// `file` is the handle `stat` was taken from, so the watch follows the file that is decoded.
    pub(crate) fn new(path: PathBuf, file: File, stat: Stat) -> Self {
        Self {
            path,
            file,
            stat,
            rewritten: AtomicBool::new(false),
        }
    }

    /// Whether the file behind the handle has a different size or modification time than when the watch began.
    /// Once true it stays true. The change time is left out on purpose: renaming or replacing the path changes it
    /// but leaves the opened file's content alone, and that handle stays valid.
    pub(crate) fn rewritten(&self) -> bool {
        if self.rewritten.load(Ordering::Acquire) {
            return true;
        }
        let same = Stat::of(&self.file)
            .is_ok_and(|now| now.size == self.stat.size && now.mtime_ns == self.stat.mtime_ns);
        if !same && !self.rewritten.swap(true, Ordering::AcqRel) {
            tracing::warn!(
                file = %self.path.display(),
                "the overlay file changed while open; no longer serving overlays from it"
            );
        }
        !same
    }
}

/// Reads the whole source from its start, hashing it and optionally keeping the bytes; checks for cancellation
/// between chunks.
pub(crate) fn read_through(
    source: &File,
    path: &Path,
    mut keep: Option<&mut Vec<u8>>,
    cancel: &CancelToken,
) -> Result<[u8; 32], Interrupt<UserError>> {
    let fail = |error: io::Error| {
        Interrupt::Failed(user_error("Cannot read the overlay file", &error, path))
    };
    let mut reader = source;
    reader.seek(SeekFrom::Start(0)).map_err(fail)?;
    let mut hasher = Sha256::new();
    let mut chunk = vec![0_u8; CHUNK];
    loop {
        cancel.check()?;
        let read = reader.read(&mut chunk).map_err(fail)?;
        if read == 0 {
            return Ok(hasher.finalize().into());
        }
        hasher.update(&chunk[..read]);
        if let Some(bytes) = keep.as_deref_mut() {
            bytes.extend_from_slice(&chunk[..read]);
        }
    }
}
