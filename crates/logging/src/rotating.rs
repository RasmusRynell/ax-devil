//! The log file that starts a new one when it grows past a size, keeping a few old ones.

use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

/// An append-only log file. When a write would take it past `limit` bytes it becomes `<name>.1`, `<name>.1` becomes
/// `<name>.2`, and so on; the file beyond `backups` is dropped. A file already past the limit when opened is rotated
/// first.
///
/// Several processes may share one path, each with its own handle: the size is read from the handle, never counted,
/// and a handle whose path now names another file (another process rotated) is replaced before the write, so a line
/// lands in the live file. Two processes rotating at the same instant can still shift the backups twice and lose one;
/// there is no lock.
///
/// Rotating is best effort: a rename or reopen that fails never fails a write, and the next attempt waits for another
/// `limit` bytes. Nothing here logs, because the logger holds this writer while it writes.
pub(crate) struct RotatingFile {
    path: PathBuf,
    limit: u64,
    backups: usize,
    /// `None` after a rotation could not reopen the file; the next write tries again.
    file: Option<File>,
    /// The length of the file past which the next write rotates it; later than `limit` when a rotation failed.
    rotate_at: u64,
}

impl RotatingFile {
    /// Opens `path` for appending, creating it, after rotating it if it is already `limit` bytes or more.
    pub(crate) fn open(path: &Path, limit: u64, backups: usize) -> io::Result<Self> {
        let mut log = Self {
            path: path.to_path_buf(),
            limit,
            backups,
            file: None,
            rotate_at: limit,
        };
        if fs::metadata(path).is_ok_and(|metadata| metadata.len() >= limit) {
            log.shift_backups();
        }
        log.reopen()?;
        Ok(log)
    }

    fn reopen(&mut self) -> io::Result<()> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        self.file = Some(file);
        self.rotate_at = self.limit;
        Ok(())
    }

    /// Whether the path still names the file `file` is open on.
    fn names(&self, file: &File) -> bool {
        let named = match fs::metadata(&self.path) {
            Ok(named) => named,
            // Only a file that is gone is replaced; one that cannot be read keeps its handle.
            Err(error) => return error.kind() != io::ErrorKind::NotFound,
        };
        let Ok(open) = file.metadata() else {
            return true;
        };
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            open.dev() == named.dev() && open.ino() == named.ino()
        }
        #[cfg(not(unix))]
        {
            open.created().ok() == named.created().ok()
        }
    }

    fn backup(&self, number: usize) -> PathBuf {
        let mut name = OsString::from(self.path.as_os_str());
        name.push(format!(".{number}"));
        PathBuf::from(name)
    }

    /// Moves every file up one number, dropping the oldest, and leaves the live path free.
    fn shift_backups(&self) {
        if self.backups == 0 {
            let _ = fs::remove_file(&self.path);
            return;
        }
        for number in (1..=self.backups).rev() {
            let from = if number == 1 {
                self.path.clone()
            } else {
                self.backup(number - 1)
            };
            // A missing file is the usual case; any other failure leaves the old file in place to be appended to.
            let _ = fs::rename(from, self.backup(number));
        }
    }

    fn rotate(&mut self) {
        // Close first: an open file cannot be renamed everywhere.
        self.file = None;
        self.shift_backups();
        if self.reopen().is_ok() {
            // A rotation that moved nothing leaves the file as long as it was; wait for another `limit` bytes.
            let len = self.file.as_ref().map_or(0, len);
            self.rotate_at = len + self.limit;
        }
    }
}

/// The length of an open file, `0` when it cannot be read.
fn len(file: &File) -> u64 {
    file.metadata().map_or(0, |metadata| metadata.len())
}

impl Write for RotatingFile {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if self.file.is_none() {
            self.reopen()?;
        }
        if self.file.as_ref().is_some_and(|file| !self.names(file)) {
            // Rotated by another process; if the live file cannot be opened, the old handle stays in use.
            let _ = self.reopen();
        }
        let size = self.file.as_ref().map_or(0, len);
        if size > 0 && size + buf.len() as u64 > self.rotate_at {
            self.rotate();
        }
        let Some(file) = self.file.as_mut() else {
            return Err(io::Error::other("the log file cannot be reopened"));
        };
        file.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file.as_mut().map_or(Ok(()), Write::flush)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_full_file_becomes_the_first_backup_and_the_oldest_backup_is_dropped() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ax-devil.log");
        let read = |name: &str| fs::read_to_string(dir.path().join(name)).ok();

        // Already past the limit when opened: rotated before the first line.
        fs::write(&path, "opened-over-the-limit\n").unwrap();
        let mut log = RotatingFile::open(&path, 10, 2).unwrap();
        for line in ["line1\n", "line2\n", "line3\n"] {
            log.write_all(line.as_bytes()).unwrap();
        }

        assert_eq!(read("ax-devil.log").as_deref(), Some("line3\n"));
        assert_eq!(read("ax-devil.log.1").as_deref(), Some("line2\n"));
        assert_eq!(read("ax-devil.log.2").as_deref(), Some("line1\n"));
        assert_eq!(read("ax-devil.log.3"), None, "the oldest backup is dropped");
    }

    #[cfg(unix)]
    #[test]
    fn a_writer_follows_the_live_file_after_another_process_rotated_it() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ax-devil.log");
        let mut first = RotatingFile::open(&path, 12, 2).unwrap();
        let mut second = RotatingFile::open(&path, 12, 2).unwrap();
        first.write_all(b"one\n").unwrap();

        // The second writer's lines fill the file, so it rotates; the first still holds the renamed file.
        second.write_all(b"two\n").unwrap();
        second.write_all(b"three\n").unwrap();
        first.write_all(b"four\n").unwrap();

        let read = |name: &str| fs::read_to_string(dir.path().join(name)).unwrap();
        assert_eq!(read("ax-devil.log"), "three\nfour\n");
        assert_eq!(read("ax-devil.log.1"), "one\ntwo\n");
    }
}
