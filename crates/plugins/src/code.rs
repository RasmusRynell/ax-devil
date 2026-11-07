//! A plugin's code identity: the manifest as it was loaded and the files it names, hashed into the cache key of its
//! output.

use std::{
    collections::BTreeMap,
    fs, io,
    path::{Path, PathBuf},
    time::SystemTime,
};

use sha2::{Digest, Sha256};

use crate::manifest::{Manifest, ManifestError, is_inside};

/// How much code a manifest may name.
#[derive(Clone, Copy)]
pub(crate) struct Limits {
    pub files: usize,
    pub bytes: u64,
}

/// A plugin is a script and a few modules; more than this is a data folder named by mistake.
pub(crate) const LIMITS: Limits = Limits {
    files: 20_000,
    bytes: 256 << 20,
};

/// One file of the identity, as stat saw it: enough to know whether it needs hashing again.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct CodeFile {
    /// Path relative to the manifest's folder, `/`-separated.
    pub relative: String,
    pub path: PathBuf,
    pub size: u64,
    pub modified: Option<SystemTime>,
}

/// The files that identify `manifest`'s code, sorted by relative path: everything its `code` paths name except the
/// manifest file, whose loaded text [`digest`] hashes instead. Dot-names and `__pycache__` folders found while walking a
/// folder are left out.
pub(crate) fn collect(manifest: &Manifest, limits: Limits) -> Result<Vec<CodeFile>, ManifestError> {
    let refuse = |reason: String| ManifestError::new("code", reason);
    let folder = manifest.folder();
    let mut files = BTreeMap::new();
    let mut bytes = 0_u64;
    let mut add =
        |path: PathBuf, files: &mut BTreeMap<String, CodeFile>| -> Result<(), ManifestError> {
            if path == manifest.path {
                return Ok(());
            }
            let relative = path.strip_prefix(folder).unwrap_or(&path);
            let relative = relative
                .components()
                .map(|part| part.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            let metadata = fs::metadata(&path)
                .map_err(|error| refuse(format!("{}: {error}", path.display())))?;
            if files.contains_key(&relative) {
                return Ok(());
            }
            bytes += metadata.len();
            if files.len() >= limits.files || bytes > limits.bytes {
                return Err(refuse(format!(
                    "names more than {} files or {} MiB of code",
                    limits.files,
                    limits.bytes >> 20
                )));
            }
            let modified = metadata.modified().ok();
            files.insert(
                relative.clone(),
                CodeFile {
                    relative,
                    path,
                    size: metadata.len(),
                    modified,
                },
            );
            Ok(())
        };

    for entry in &manifest.code {
        let named = Path::new(entry);
        if !is_inside(named) {
            return Err(refuse(format!(
                "'{entry}' must be a path inside the plugin's folder"
            )));
        }
        let start = folder.join(named);
        let mut pending = vec![start];
        while let Some(path) = pending.pop() {
            let metadata = fs::metadata(&path)
                .map_err(|error| refuse(format!("{}: {error}", path.display())))?;
            if metadata.is_file() {
                add(path, &mut files)?;
            } else if metadata.is_dir() {
                let entries = fs::read_dir(&path)
                    .map_err(|error| refuse(format!("{}: {error}", path.display())))?;
                for child in entries {
                    let child =
                        child.map_err(|error| refuse(format!("{}: {error}", path.display())))?;
                    let skipped = child.file_name().to_string_lossy().starts_with('.')
                        || child.file_name() == "__pycache__";
                    // A symlinked folder could loop; a symlinked file is code like any other.
                    let linked_dir = child.file_type().is_ok_and(|kind| kind.is_symlink())
                        && child.path().is_dir();
                    if !skipped && !linked_dir {
                        pending.push(child.path());
                    }
                }
            }
        }
    }
    Ok(files.into_values().collect())
}

/// SHA-256 over the manifest's digest, then `path \0 length \0 bytes` of each file, in the order given.
pub(crate) fn digest(manifest: &Manifest, files: &[CodeFile]) -> io::Result<[u8; 32]> {
    let mut hash = Sha256::new();
    hash.update(manifest.digest);
    for file in files {
        let bytes = fs::read(&file.path)?;
        hash.update(file.relative.as_bytes());
        hash.update([0]);
        hash.update(bytes.len().to_string().as_bytes());
        hash.update([0]);
        hash.update(&bytes);
    }
    Ok(hash.finalize().into())
}
