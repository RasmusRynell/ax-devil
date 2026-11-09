//! The files and folders an item was read from, and what the disk looked like at each, so a later look can tell that
//! one changed.

use crate::{Content, Recipe, Source, item::check_settings};
use foundation::{CancelToken, Cancelled, Interrupt};
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
    /// reason to resolve again), and the folders and files a playlist resolver's settings name, with the files it
    /// asks to have watched. A resolver that is not installed, or fails to name its files, names none of those.
    ///
    /// # Errors
    /// `cancel` was cancelled.
    pub fn of_recipe(
        recipe: &Recipe,
        handlers: &Handlers,
        cancel: &CancelToken,
    ) -> Result<Self, Cancelled> {
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
                    return Ok(paths);
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
                if let Ok(values) =
                    check_settings(&resolver.spec().form, &playlist.settings, handlers)
                {
                    match resolver.watched(&values, cancel) {
                        Ok(files) => paths.files.extend(files),
                        Err(Interrupt::Cancelled) => return Err(Cancelled),
                        Err(Interrupt::Failed(error)) => tracing::warn!(
                            "Playlist resolver '{}' could not name its watched files: {}",
                            playlist.resolver,
                            error.reason
                        ),
                    }
                }
            }
            Source::Live(_) | Source::Unreadable(_) => {}
        }
        Ok(paths)
    }

    /// Adds the overlay files `content` names, which are read whatever the recipe said, and returns the ones that
    /// were not watched before.
    fn add_content(&mut self, content: &Content) -> Self {
        let mut added = Self::default();
        for entry in content.entries() {
            for overlay in &entry.overlays {
                if self.files.insert(overlay.path.clone()) {
                    added.files.insert(overlay.path.clone());
                }
            }
        }
        added
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
        let (mut walked, mut truncated) = (0, false);
        let mut pending: Vec<PathBuf> = self.folders.iter().rev().cloned().collect();
        while let Some(folder) = pending.pop() {
            token.check()?;
            if walked == MAX_FOLDERS {
                truncated = true;
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
        Ok(Snapshot { prints, truncated })
    }
}

/// What an item was read from, and what the disk looked like at those paths before they were read, so a change made
/// while the item was being read still counts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Watch {
    paths: SourcePaths,
    baseline: Snapshot,
}

impl Watch {
    /// Runs `read`, which resolves the item `recipe` belongs to, between two looks at the disk, and returns its result
    /// with what it read: the recipe's paths as they were before `read` began (a path that was absent then stays
    /// absent in the baseline, so its appearance is a change), and the overlay files the Content names that the recipe
    /// did not. `None` when there is nothing to watch or `cancel` was triggered.
    pub fn read<E>(
        recipe: &Recipe,
        handlers: &Handlers,
        cancel: &CancelToken,
        read: impl FnOnce() -> Result<Content, E>,
    ) -> (Result<Content, E>, Option<Self>) {
        let Ok(mut paths) = SourcePaths::of_recipe(recipe, handlers, cancel) else {
            return (read(), None);
        };
        let before = paths.snapshot(cancel);
        let result = read();
        let (Ok(mut baseline), false) = (before, cancel.is_cancelled()) else {
            return (result, None);
        };
        if let Ok(content) = &result {
            match paths.add_content(content).snapshot(cancel) {
                Ok(extra) => baseline = baseline.or(extra),
                Err(Cancelled) => return (result, None),
            }
        }
        if baseline.truncated {
            tracing::warn!(
                "A source spans over {MAX_FOLDERS} folders; changes in the rest go unnoticed: {:?}",
                paths.folders
            );
        }
        let watch = (!paths.is_empty()).then_some(Self { paths, baseline });
        (result, watch)
    }

    /// What the disk looks like at the watched paths now.
    ///
    /// # Errors
    /// `token` was cancelled.
    pub fn look(&self, token: &CancelToken) -> Result<Snapshot, Cancelled> {
        self.paths.snapshot(token)
    }

    /// The paths that differ in `now` from what was read.
    pub fn changes(&self, now: &Snapshot) -> BTreeSet<PathBuf> {
        self.baseline.changes(now)
    }

    /// Takes `now` as what was read, so what it shows is no longer a change.
    pub fn accept(&mut self, now: Snapshot) {
        self.baseline = now;
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
    /// Whether folders past [`MAX_FOLDERS`] were left out.
    truncated: bool,
}

impl Snapshot {
    /// This snapshot together with the paths of `other`, which must be paths this one did not look at.
    #[must_use]
    fn or(mut self, other: Self) -> Self {
        self.prints.extend(other.prints);
        self.truncated |= other.truncated;
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
