//! The files ax-devil keeps in the cache folder (frame indexes and decoded overlay artifacts), found and removed for
//! `clear-cache`.
//!
//! Only files of the extensions ax-devil writes, directly in its own subfolders, count: a `--cache-dir` shared with
//! other files loses nothing else, and symbolic links are never followed.

use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// Where each kind of cache file lives under the cache folder: its label, subfolder and file extension.
const KINDS: [(&str, &str, &str); 2] = [
    ("Frame indexes", media::INDEX_FOLDER, media::INDEX_EXTENSION),
    (
        "Overlay scenes",
        scene_store::SCENE_FOLDER,
        scene_store::SCENE_EXTENSION,
    ),
];

/// The cache files of one kind.
#[derive(Debug)]
pub struct Location {
    /// What the files are.
    pub label: &'static str,
    /// The folder they are in.
    pub folder: PathBuf,
    files: Vec<(PathBuf, u64)>,
}

impl Location {
    /// How many files there are.
    pub fn file_count(&self) -> usize {
        self.files.len()
    }

    /// Their size together, in bytes.
    pub fn bytes(&self) -> u64 {
        self.files.iter().map(|(_, bytes)| bytes).sum()
    }
}

/// What [`clear`] removed and what it could not.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Cleared {
    /// How many files were removed.
    pub files: usize,
    /// The size of the removed files, in bytes, as scanned.
    pub bytes: u64,
    /// One line for each file that could not be removed: its path and the reason.
    pub failures: Vec<String>,
}

/// The locations under `cache_dir` that hold cache files. A folder that does not exist holds none.
pub fn scan(cache_dir: &Path) -> Result<Vec<Location>, String> {
    let mut found = Vec::new();
    for (label, subfolder, extension) in KINDS {
        let folder = cache_dir.join(subfolder);
        let files = match files_in(&folder, extension) {
            Ok(files) => files,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("Cannot read {}: {error}", folder.display())),
        };
        if !files.is_empty() {
            found.push(Location {
                label,
                folder,
                files,
            });
        }
    }
    Ok(found)
}

fn files_in(folder: &Path, extension: &str) -> io::Result<Vec<(PathBuf, u64)>> {
    let mut files = Vec::new();
    for entry in fs::read_dir(folder)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().is_some_and(|found| found == extension) {
            let metadata = entry.metadata()?;
            if metadata.is_file() {
                files.push((path, metadata.len()));
            }
        }
    }
    Ok(files)
}

/// Removes the files of `locations`, counting the ones it removed. A file that is already gone is not counted and
/// not a failure; any other failure is listed and the rest are still removed.
pub fn clear(locations: &[Location]) -> Cleared {
    let mut cleared = Cleared::default();
    for (path, bytes) in locations.iter().flat_map(|location| &location.files) {
        match fs::remove_file(path) {
            Ok(()) => {
                cleared.files += 1;
                cleared.bytes += bytes;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => cleared
                .failures
                .push(format!("Cannot remove {}: {error}", path.display())),
        }
    }
    cleared
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clearing_removes_only_cache_files_and_counts_what_it_removed() {
        let cache = tempfile::tempdir().unwrap();
        let write = |path: &str, bytes: usize| {
            let path = cache.path().join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, vec![0; bytes]).unwrap();
        };
        write("frame-index/a-1.axidx", 100);
        write("frame-index/b-2.axidx", 28);
        write("scenes/c.axscene", 1000);
        write("frame-index/notes.txt", 5);
        write("settings.json", 5);
        write("scenes/nested/d.axscene", 5);

        let found = scan(cache.path()).unwrap();
        let counts: Vec<_> = found
            .iter()
            .map(|location| (location.file_count(), location.bytes()))
            .collect();
        assert_eq!(counts, [(2, 128), (1, 1000)]);

        let cleared = clear(&found);
        assert_eq!((cleared.files, cleared.bytes), (3, 1128));
        assert!(cleared.failures.is_empty());
        assert!(scan(cache.path()).unwrap().is_empty());
        for kept in [
            "frame-index/notes.txt",
            "settings.json",
            "scenes/nested/d.axscene",
        ] {
            assert!(cache.path().join(kept).exists(), "{kept} was removed");
        }
        assert!(scan(&cache.path().join("missing")).unwrap().is_empty());
    }
}
