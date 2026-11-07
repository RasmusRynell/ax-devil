//! Recovery copies: where unsaved work is kept, and the lock that gives each copy to one window.

use crate::{
    SUFFIX,
    disk::{file_name, read_file, write_file},
    document::{normalize, to_document},
};
use foundation::{ErrorCode, HostPath, UserError};
use std::{
    fs::{self, File, OpenOptions, TryLockError},
    io,
    path::{Path, PathBuf},
};
use workspace::Workspace;

/// The folder that holds the recovery copies.
pub struct RecoveryFolder {
    dir: PathBuf,
}

/// Why a copy could not be claimed.
#[derive(Debug)]
pub enum Claim {
    /// Another window or process holds it.
    InUse,
    /// It could not be opened.
    Failed(UserError),
}

/// A recovery copy this window holds. Dropping it releases the lock and keeps the file.
#[derive(Debug)]
pub struct RecoveryCopy {
    path: PathBuf,
    /// Held for as long as the copy is.
    _lock: File,
}

impl RecoveryFolder {
    /// The folder `dir`, made absolute; it is created with the first copy.
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir: normalize(&dir),
        }
    }

    /// Whether `path` is inside this folder once symlinks and `..` are resolved, so an alias is the file it names.
    pub fn contains(&self, path: &Path) -> bool {
        let (path, dir) = (resolved(path), resolved(&self.dir));
        path != dir && path.starts_with(dir)
    }

    /// Creates and locks a new empty copy named `<stem><SUFFIX>`, then `<stem> 2<SUFFIX>`, `<stem> 3<SUFFIX>`, and so
    /// on. When the file system refuses the name, uses "Unsaved workspace".
    pub fn reserve(&self, stem: &str) -> io::Result<RecoveryCopy> {
        create_dir(&self.dir)?;
        self.reserve_named(stem)
            .or_else(|_| self.reserve_named("Unsaved workspace"))
    }

    fn reserve_named(&self, stem: &str) -> io::Result<RecoveryCopy> {
        let mut n = 1;
        loop {
            let name = if n == 1 {
                format!("{stem}{SUFFIX}")
            } else {
                format!("{stem} {n}{SUFFIX}")
            };
            let path = self.dir.join(name);
            let taken = match lock_beside(&path) {
                Ok(lock) => new_file(&path).map(|()| lock),
                Err(TryLockError::WouldBlock) => Err(io::ErrorKind::AlreadyExists.into()),
                Err(TryLockError::Error(error)) => Err(error),
            };
            match taken {
                Ok(lock) => {
                    return Ok(RecoveryCopy { path, _lock: lock });
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => n += 1,
                Err(error) => return Err(error),
            }
        }
    }

    /// Locks the existing copy `path`.
    pub fn claim(&self, path: &Path) -> Result<RecoveryCopy, Claim> {
        // Named as this folder spells it, so an alias claims the same lock and the same recent entry.
        let path = match resolved(path).strip_prefix(resolved(&self.dir)) {
            Ok(inside) => self.dir.join(inside),
            Err(_) => normalize(path),
        };
        let missing = || {
            let reason = format!("{} was not found.", file_name(&path));
            let error = UserError::new(ErrorCode::FileNotFound, reason).at(HostPath::new(&path));
            Claim::Failed(error)
        };
        if !path.is_file() {
            return Err(missing());
        }
        match lock_beside(&path) {
            // The holder before us may have deleted the copy after our check.
            Ok(_) if !path.is_file() => {
                let _ = fs::remove_file(lock_path(&path));
                Err(missing())
            }
            Ok(lock) => Ok(RecoveryCopy { path, _lock: lock }),
            Err(TryLockError::WouldBlock) => Err(Claim::InUse),
            Err(TryLockError::Error(error)) => {
                let reason = format!("Cannot open {}: {error}", file_name(&path));
                let error =
                    UserError::new(ErrorCode::FileUnreadable, reason).at(HostPath::new(&path));
                Err(Claim::Failed(error))
            }
        }
    }
}

impl RecoveryCopy {
    /// The copy's file.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Whether `path` names this copy, possibly through a symlink.
    pub fn is_at(&self, path: &Path) -> bool {
        resolved(path) == resolved(&self.path)
    }

    /// Replaces the copy with `workspace` atomically. Paths are written absolute; `recovers` is the workspace file
    /// the copy belongs to, `None` for Untitled.
    pub fn write(&self, workspace: &Workspace, recovers: Option<&Path>) -> Result<(), UserError> {
        let document = to_document(workspace, None, Some(recovers));
        write_file(&self.path, &document).map_err(|error| {
            let reason = format!("Could not save {}: {error}", file_name(&self.path));
            UserError::new(ErrorCode::SaveFailed, reason).at(HostPath::new(&self.path))
        })
    }

    /// The workspace in the copy and the file it belongs to.
    pub fn read(&self) -> Result<(Workspace, Option<PathBuf>), UserError> {
        read_file(&self.path)
    }

    /// Removes the copy and its lock file. The lock is kept until this copy is dropped, so a failure can be retried.
    pub fn delete(&self) -> io::Result<()> {
        let removed = fs::remove_file(&self.path);
        // Unlinked while still locked: nobody can lock this sidecar and find the copy gone under them. A copy that
        // is still there keeps its lock.
        if removed.is_ok()
            || removed
                .as_ref()
                .is_err_and(|e| e.kind() == io::ErrorKind::NotFound)
        {
            let _ = fs::remove_file(lock_path(&self.path));
        }
        match removed {
            Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
            _ => Ok(()),
        }
    }
}

/// A file name stem for a workspace named `name`: path separators, control characters and the characters Windows
/// reserves in names (`:` also starts a drive prefix or an NTFS stream, which would put the copy outside its folder)
/// become `-`, as does a name of only dots; no name is "Untitled". Cut to 64 bytes.
pub fn file_stem(name: Option<&str>) -> String {
    let Some(name) = name else {
        return "Untitled".into();
    };
    let mut stem: String = name
        .chars()
        .map(|c| {
            if matches!(c, '/' | '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*') || c.is_control() {
                '-'
            } else {
                c
            }
        })
        .collect();
    while stem.len() > 64 {
        stem.pop();
    }
    if stem.chars().all(|c| c == '.') {
        stem = "-".into();
    }
    stem
}

/// The lock file beside the copy: the atomic replace gives the copy a new inode, so the lock cannot live on it.
/// Where `path` really is, whether or not it exists yet: its symlinks followed, or its folder's. Two paths name the
/// same file when they resolve alike.
pub fn resolved(path: &Path) -> PathBuf {
    let path = normalize(path);
    if let Ok(real) = fs::canonicalize(&path) {
        return real;
    }
    match (path.parent().map(fs::canonicalize), path.file_name()) {
        (Some(Ok(folder)), Some(name)) => folder.join(name),
        _ => path,
    }
}

fn lock_path(copy: &Path) -> PathBuf {
    let mut path = copy.as_os_str().to_owned();
    path.push(".lock");
    PathBuf::from(path)
}

/// Opens the lock file of `copy` and locks it without waiting. The OS releases it when the process ends. A holder
/// unlinks the file while it holds the lock, so a lock only counts when the file is still the one at the path;
/// otherwise start over.
fn lock_beside(copy: &Path) -> Result<File, TryLockError> {
    let path = lock_path(copy);
    loop {
        let mut options = OpenOptions::new();
        options.create(true).write(true).truncate(false);
        private(&mut options);
        let lock = options.open(&path).map_err(TryLockError::Error)?;
        lock.try_lock()?;
        if is_at(&lock, &path) {
            return Ok(lock);
        }
    }
}

/// Whether `file` is the file now at `path`.
#[cfg(unix)]
fn is_at(file: &File, path: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (file.metadata(), fs::metadata(path)) {
        (Ok(file), Ok(at)) => (file.dev(), file.ino()) == (at.dev(), at.ino()),
        _ => false,
    }
}

/// Whether the lock file is still there; the platform's open files cannot be compared with the standard library.
#[cfg(not(unix))]
fn is_at(_: &File, path: &Path) -> bool {
    path.exists()
}

fn new_file(path: &Path) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    private(&mut options);
    options.open(path).map(drop)
}

#[cfg(unix)]
fn private(options: &mut OpenOptions) {
    std::os::unix::fs::OpenOptionsExt::mode(options, 0o600);
}

#[cfg(not(unix))]
fn private(_: &mut OpenOptions) {}

fn create_dir(dir: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
    builder.create(dir)
}
