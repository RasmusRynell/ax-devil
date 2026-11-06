//! The files and folders an item was read from, and what the disk looked like at each, so a later look can tell that
//! one changed.

use crate::{Content, Recipe, Source};
use foundation::{CancelToken, Cancelled};
use handler_api::FieldKind;
use handlers::Handlers;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

/// The most folders one snapshot walks; the rest are left out.
pub const MAX_FOLDERS: usize = 4096;

/// The files and folders an item is read from. A folder counts with every subfolder, for which files it holds: a file
/// inside it that is rewritten in place is not noticed, so a file whose contents matter is named as a file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SourcePaths {
    files: BTreeSet<PathBuf>,
    folders: BTreeSet<PathBuf>,
}

impl SourcePaths {
    /// What `recipe` reads before it has any Content: a video's overlay files (a change to the video itself is no
    /// reason to resolve again), and the folders and files a playlist resolver's settings name. A resolver that is
    /// not installed names none.
    pub fn of_recipe(recipe: &Recipe, handlers: &Handlers) -> Self {
        let mut paths = Self::default();
        match &recipe.source {
            Source::Video(video) => {
                paths.files.extend(
                    video
                        .overlays
                        .iter()
                        .map(|o| o.path.as_path().to_path_buf()),
                );
            }
            Source::Playlist(playlist) => {
                let Some(resolver) = handlers.resolver(&playlist.resolver) else {
                    return paths;
                };
                for field in &resolver.spec().form.fields {
                    let Some(Value::String(path)) = playlist.settings.get(&field.key) else {
                        continue;
                    };
                    if path.is_empty() {
                        continue;
                    }
                    match field.kind {
                        FieldKind::Folder => paths.folders.insert(PathBuf::from(path)),
                        FieldKind::File { .. } => paths.files.insert(PathBuf::from(path)),
                        _ => false,
                    };
                }
            }
            Source::Live(_) | Source::Unreadable(_) => {}
        }
        paths
    }

    /// Adds the overlay files `content` names, which are read whatever the recipe said.
    pub fn add_content(&mut self, content: &Content) {
        for entry in content.entries() {
            self.files
                .extend(entry.overlays.iter().map(|overlay| overlay.path.clone()));
        }
    }

    /// Whether there is nothing to watch.
    pub fn is_empty(&self) -> bool {
        self.files.is_empty() && self.folders.is_empty()
    }

    /// What the disk looks like at these paths now. At most [`MAX_FOLDERS`] folders are walked in all, symbolic links
    /// not followed; a path that does not exist is absent.
    ///
    /// # Errors
    /// `token` was cancelled.
    pub fn snapshot(&self, token: &CancelToken) -> Result<Snapshot, Cancelled> {
        let mut prints = BTreeMap::new();
        for file in &self.files {
            token.check()?;
            if let Some(print) = Fingerprint::of(file) {
                prints.insert(file.clone(), print);
            }
        }
        let mut walked = 0;
        let mut pending: Vec<PathBuf> = self.folders.iter().rev().cloned().collect();
        while let Some(folder) = pending.pop() {
            token.check()?;
            if walked == MAX_FOLDERS {
                break;
            }
            walked += 1;
            let Some(print) = Fingerprint::of(&folder) else {
                continue;
            };
            prints.insert(folder.clone(), print);
            for entry in fs::read_dir(&folder).into_iter().flatten().flatten() {
                if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                    pending.push(entry.path());
                }
            }
        }
        Ok(Snapshot { prints })
    }
}

/// A file's or folder's identity, size and modification time: a file's changes when it is written or replaced, a
/// folder's when an entry in it is added, removed or renamed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Fingerprint {
    inode: u64,
    size: u64,
    mtime_ns: i128,
}

impl Fingerprint {
    /// The fingerprint of `path`, following a symbolic link; `None` when it cannot be read.
    fn of(path: &Path) -> Option<Self> {
        let metadata = fs::metadata(path).ok()?;
        let mtime_ns = metadata.modified().ok().map_or(0, |time| {
            match time.duration_since(std::time::UNIX_EPOCH) {
                Ok(since) => i128::try_from(since.as_nanos()).unwrap_or(i128::MAX),
                Err(before) => -i128::try_from(before.duration().as_nanos()).unwrap_or(i128::MAX),
            }
        });
        #[cfg(unix)]
        let inode = std::os::unix::fs::MetadataExt::ino(&metadata);
        #[cfg(not(unix))]
        let inode = 0;
        Some(Self {
            inode,
            size: metadata.len(),
            mtime_ns,
        })
    }
}

/// What the disk looked like at some [`SourcePaths`] at one moment.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Snapshot {
    prints: BTreeMap<PathBuf, Fingerprint>,
}

impl Snapshot {
    /// This snapshot together with `other`'s paths it lacks: for a path in both, this one's fingerprint stays, so a
    /// change made while the item was being read is still a change.
    #[must_use]
    pub fn or(mut self, other: Self) -> Self {
        for (path, print) in other.prints {
            self.prints.entry(path).or_insert(print);
        }
        self
    }

    /// The paths that were added, removed or changed from this snapshot to `later`.
    pub fn changes(&self, later: &Self) -> BTreeSet<PathBuf> {
        self.prints
            .keys()
            .chain(later.prints.keys())
            .filter(|path| self.prints.get(*path) != later.prints.get(*path))
            .cloned()
            .collect()
    }

    /// Whether `other` has the same fingerprint as this one at each of `paths`.
    pub fn agrees_at(&self, other: &Self, paths: &BTreeSet<PathBuf>) -> bool {
        paths
            .iter()
            .all(|path| self.prints.get(path) == other.prints.get(path))
    }
}

#[cfg(test)]
mod tests;
