//! The files ax-devil keeps in the cache folder (frame indexes and decoded overlay artifacts), found and removed for
//! `clear-cache`.
//!
//! Only files of the extensions ax-devil writes, directly in its own subfolders, count: a `--cache-dir` shared with
//! other files loses nothing else, and symbolic links are never followed: not a file's, and not a subfolder's, so a
//! link to a folder elsewhere is skipped whole. Removal checks the subfolder again just before it removes from it;
//! std cannot remove relative to an opened folder, so a swap within that instant is not excluded.

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

/// What [`scan`] found.
#[derive(Debug, Default)]
pub struct Scan {
    /// The locations that hold cache files.
    pub locations: Vec<Location>,
    /// Cache subfolders that are not plain folders (a symbolic link, say) and were left alone.
    pub skipped: Vec<PathBuf>,
}

/// A real folder; a symbolic link to one is not.
fn is_plain_folder(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir())
}

/// The locations under `cache_dir` that hold cache files. A folder that does not exist holds none.
pub fn scan(cache_dir: &Path) -> Result<Scan, String> {
    let mut found = Scan::default();
    for (label, subfolder, extension) in KINDS {
        let folder = cache_dir.join(subfolder);
        if fs::symlink_metadata(&folder).is_ok() && !is_plain_folder(&folder) {
            found.skipped.push(folder);
            continue;
        }
        let files = match files_in(&folder, extension) {
            Ok(files) => files,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("Cannot read {}: {error}", folder.display())),
        };
        if !files.is_empty() {
            found.locations.push(Location {
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
    for location in locations {
        if !is_plain_folder(&location.folder) {
            cleared.failures.push(format!(
                "Cannot remove from {}: not a plain folder, skipped",
                location.folder.display()
            ));
            continue;
        }
        for (path, bytes) in &location.files {
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

        let found = scan(cache.path()).unwrap().locations;
        let counts: Vec<_> = found
            .iter()
            .map(|location| (location.file_count(), location.bytes()))
            .collect();
        assert_eq!(counts, [(2, 128), (1, 1000)]);

        let cleared = clear(&found);
        assert_eq!((cleared.files, cleared.bytes), (3, 1128));
        assert!(cleared.failures.is_empty());
        assert!(scan(cache.path()).unwrap().locations.is_empty());
        for kept in [
            "frame-index/notes.txt",
            "settings.json",
            "scenes/nested/d.axscene",
        ] {
            assert!(cache.path().join(kept).exists(), "{kept} was removed");
        }
        assert!(
            scan(&cache.path().join("missing"))
                .unwrap()
                .locations
                .is_empty()
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_subfolder_that_is_a_link_is_skipped_and_what_it_points_to_survives() {
        let cache = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let external = outside.path().join("keep.axscene");
        fs::write(&external, [0; 10]).unwrap();
        std::os::unix::fs::symlink(outside.path(), cache.path().join("scenes")).unwrap();

        let found = scan(cache.path()).unwrap();
        assert!(found.locations.is_empty());
        assert_eq!(found.skipped, [cache.path().join("scenes")]);

        // Swapped for a link after the scan: the removal checks again.
        let inside = cache.path().join("frame-index");
        fs::create_dir(&inside).unwrap();
        fs::write(inside.join("a.axidx"), [0; 5]).unwrap();
        let found = scan(cache.path()).unwrap().locations;
        fs::remove_dir_all(&inside).unwrap();
        std::os::unix::fs::symlink(outside.path(), &inside).unwrap();
        fs::write(outside.path().join("a.axidx"), [0; 5]).unwrap();
        let cleared = clear(&found);
        assert_eq!(cleared.files, 0);
        assert_eq!(cleared.failures.len(), 1);
        assert!(external.exists() && outside.path().join("a.axidx").exists());
    }
}
