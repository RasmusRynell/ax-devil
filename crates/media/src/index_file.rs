//! The frame index as a file: its bytes, where it lives under the cache folder, and how it is written.
//!
//! Layout, all little-endian: `"AXDVIDX"`, version `u8`, scan kind `u8`, the fingerprint (`device`, `inode`, `size`
//! as `u64`; `mtime_ns`, `ctime_ns` as `i64`; the sample digest as `u64`), then the body: stream `u32`, time base
//! numerator and denominator `i32`, first pts `i64`, the duration of the last frame in time base ticks `i64` (0
//! when the container gave none), frame count and keyframe count `u64`, the pts of every frame
//! in presentation order `i64[]`, the keyframe sequence ids `u64[]`, and last the xxh3 of the body.

use crate::ScanKind;
use crate::fingerprint::Fingerprint;
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use xxhash_rust::xxh3::xxh3_64;

const MAGIC: &[u8; 7] = b"AXDVIDX";
const VERSION: u8 = 2;
const FINGERPRINT_BYTES: usize = 6 * 8;
const HEAD_BYTES: usize = MAGIC.len() + 2 + FINGERPRINT_BYTES;
const FIXED_BODY_BYTES: usize = 4 + 4 + 4 + 8 + 8 + 8 + 8;
/// The folder under the cache folder that holds the index files.
pub const INDEX_FOLDER: &str = "frame-index";
/// The extension of an index file.
pub const INDEX_EXTENSION: &str = "axidx";
/// The longest part of an index file name taken from the video's own name, in UTF-8 bytes.
const NAME_BYTES: usize = 120;

/// Everything the file keeps about an index.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Stored {
    pub(crate) scan: ScanKind,
    pub(crate) fingerprint: Fingerprint,
    pub(crate) stream: u32,
    pub(crate) time_base: (i32, i32),
    pub(crate) first_pts: i64,
    /// The container's duration of the last frame in presentation order, in time base ticks; 0 when it gave none.
    pub(crate) last_duration: i64,
    /// The pts of every frame in presentation order.
    pub(crate) pts: Vec<i64>,
    /// The sequence ids where decoding can start.
    pub(crate) keyframes: Vec<u64>,
}

impl Stored {
    /// The bytes of the index file.
    pub(crate) fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(
            HEAD_BYTES + FIXED_BODY_BYTES + 8 * (self.pts.len() + self.keyframes.len()) + 8,
        );
        out.extend_from_slice(MAGIC);
        out.push(VERSION);
        out.push(match self.scan {
            ScanKind::Packet => 0,
            ScanKind::Decode => 1,
        });
        let print = &self.fingerprint;
        out.extend_from_slice(&print.device.to_le_bytes());
        out.extend_from_slice(&print.inode.to_le_bytes());
        out.extend_from_slice(&print.size.to_le_bytes());
        out.extend_from_slice(&print.mtime_ns.to_le_bytes());
        out.extend_from_slice(&print.ctime_ns.to_le_bytes());
        out.extend_from_slice(&print.sample.to_le_bytes());
        let body_start = out.len();
        out.extend_from_slice(&self.stream.to_le_bytes());
        out.extend_from_slice(&self.time_base.0.to_le_bytes());
        out.extend_from_slice(&self.time_base.1.to_le_bytes());
        out.extend_from_slice(&self.first_pts.to_le_bytes());
        out.extend_from_slice(&self.last_duration.to_le_bytes());
        out.extend_from_slice(&(self.pts.len() as u64).to_le_bytes());
        out.extend_from_slice(&(self.keyframes.len() as u64).to_le_bytes());
        for pts in &self.pts {
            out.extend_from_slice(&pts.to_le_bytes());
        }
        for keyframe in &self.keyframes {
            out.extend_from_slice(&keyframe.to_le_bytes());
        }
        let checksum = xxh3_64(&out[body_start..]);
        out.extend_from_slice(&checksum.to_le_bytes());
        out
    }

    /// Reads an index file, or says what is wrong with it.
    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, &'static str> {
        let mut reader = Reader(bytes);
        if reader.take(MAGIC.len())? != MAGIC {
            return Err("not an index file");
        }
        if reader.u8()? != VERSION {
            return Err("another version");
        }
        let scan = match reader.u8()? {
            0 => ScanKind::Packet,
            1 => ScanKind::Decode,
            _ => return Err("unknown scan kind"),
        };
        let fingerprint = Fingerprint {
            device: reader.u64()?,
            inode: reader.u64()?,
            size: reader.u64()?,
            mtime_ns: reader.i64()?,
            ctime_ns: reader.i64()?,
            sample: reader.u64()?,
        };
        let body = reader.0;
        let (body, checksum) = body
            .split_at_checked(body.len().saturating_sub(8))
            .ok_or("too short")?;
        if checksum != xxh3_64(body).to_le_bytes() {
            return Err("checksum mismatch");
        }
        let mut reader = Reader(body);
        let stream = reader.u32()?;
        let time_base = (reader.i32()?, reader.i32()?);
        let first_pts = reader.i64()?;
        let last_duration = reader.i64()?;
        let frames = reader.u64()?;
        let keys = reader.u64()?;
        let expected = frames
            .checked_add(keys)
            .and_then(|count| count.checked_mul(8))
            .ok_or("impossible counts")?;
        if usize::try_from(expected) != Ok(reader.0.len()) {
            return Err("wrong length");
        }
        let (frames, keys) = (frames as usize, keys as usize);
        let pts = (0..frames)
            .map(|_| reader.i64())
            .collect::<Result<_, _>>()?;
        let keyframes = (0..keys).map(|_| reader.u64()).collect::<Result<_, _>>()?;
        Ok(Self {
            scan,
            fingerprint,
            stream,
            time_base,
            first_pts,
            last_duration,
            pts,
            keyframes,
        })
    }
}

/// A cursor over bytes that reports running out as an error.
struct Reader<'a>(&'a [u8]);

impl<'a> Reader<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8], &'static str> {
        let (taken, rest) = self.0.split_at_checked(count).ok_or("too short")?;
        self.0 = rest;
        Ok(taken)
    }

    fn array<const N: usize>(&mut self) -> Result<[u8; N], &'static str> {
        self.take(N)?.try_into().map_err(|_| "too short")
    }

    fn u8(&mut self) -> Result<u8, &'static str> {
        Ok(self.array::<1>()?[0])
    }

    fn u32(&mut self) -> Result<u32, &'static str> {
        self.array().map(u32::from_le_bytes)
    }

    fn i32(&mut self) -> Result<i32, &'static str> {
        self.array().map(i32::from_le_bytes)
    }

    fn u64(&mut self) -> Result<u64, &'static str> {
        self.array().map(u64::from_le_bytes)
    }

    fn i64(&mut self) -> Result<i64, &'static str> {
        self.array().map(i64::from_le_bytes)
    }
}

/// Where the index of the video at `canonical` lives: `<cache_dir>/frame-index/<name>-<hash>.axidx`, where the name
/// is the video's file name cut to at most 120 UTF-8 bytes on a character boundary, and the hash is the first 16
/// hex digits of the SHA-256 of the whole canonical path, which tells apart files of the same name.
pub(crate) fn path_for(cache_dir: &Path, canonical: &Path) -> PathBuf {
    let name = canonical
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let name: String = name
        .chars()
        .map(|c| {
            if c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') {
                '_'
            } else {
                c
            }
        })
        .scan(0, |used, c| {
            *used += c.len_utf8();
            (*used <= NAME_BYTES).then_some(c)
        })
        .collect();
    let digest = Sha256::digest(canonical.as_os_str().as_encoded_bytes());
    let hash: String = digest
        .iter()
        .take(8)
        .map(|byte| format!("{byte:02x}"))
        .collect();
    cache_dir
        .join(INDEX_FOLDER)
        .join(format!("{name}-{hash}.{INDEX_EXTENSION}"))
}

/// Writes `path` so that readers see the old file or the new one, never part of one: the content goes to a private
/// temporary file beside it, which replaces `path` only after it is complete and on disk. A failed write removes
/// the temporary file and leaves `path` as it was.
pub(crate) fn write_atomic(
    path: &Path,
    write: impl FnOnce(&mut File) -> io::Result<()>,
) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Err(io::Error::other("the index path has no folder"));
    };
    create_private_dir(parent)?;
    static WRITES: AtomicU64 = AtomicU64::new(0);
    let unique = WRITES.fetch_add(1, Ordering::Relaxed);
    let temporary = path.with_extension(format!("{}-{unique}.tmp", std::process::id()));
    let result = (|| {
        let mut file = private_file_options().open(&temporary)?;
        write(&mut file)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        // Best effort: a leftover temporary file is ignored by readers and replaced by the next write.
        let _ = fs::remove_file(&temporary);
    }
    result
}

/// Writes `index` to `path` atomically.
pub(crate) fn save(path: &Path, index: &Stored) -> io::Result<()> {
    write_atomic(path, |file| file.write_all(&index.encode()))
}

/// Reads and decodes the index file at `path`.
pub(crate) fn load(path: &Path) -> Result<Stored, String> {
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    Stored::decode(&bytes).map_err(str::to_string)
}

#[cfg(unix)]
fn create_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

#[cfg(not(unix))]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

#[cfg(unix)]
fn private_file_options() -> OpenOptions {
    use std::os::unix::fs::OpenOptionsExt;
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true).mode(0o600);
    options
}

#[cfg(not(unix))]
fn private_file_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    options
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_failed_write_leaves_the_old_file_or_none_and_no_temporary_file() {
        let dir = tempfile::tempdir().expect("temp dir");
        let path = dir.path().join("frame-index").join("a.axidx");
        let interrupted = |file: &mut File| {
            file.write_all(b"half of the new content")?;
            Err(io::Error::other("the disk filled up"))
        };

        assert!(write_atomic(&path, interrupted).is_err());
        assert!(!path.exists(), "no file when nothing complete was written");

        write_atomic(&path, |file| file.write_all(b"old")).expect("first write");
        assert!(write_atomic(&path, interrupted).is_err());
        assert_eq!(fs::read(&path).expect("old file"), b"old");
        let leftovers = fs::read_dir(path.parent().expect("folder"))
            .expect("list")
            .count();
        assert_eq!(leftovers, 1, "only the index file remains");
    }
}
