//! What the last load of the plugins read, which tells whether and when to load again.

use plugins::Registry;
use std::{
    fs,
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};

/// Length and modification time of the registry file and of each manifest it lists, which is what tells that
/// something was installed, removed or edited. Code files are not in it: a plugin's code is looked at on each use.
#[derive(Default, PartialEq, Eq)]
pub(super) struct Stamp(Vec<(PathBuf, Option<(u64, u128)>)>);

impl Stamp {
    pub(super) fn of(registry: &Path) -> Self {
        let mut files = vec![registry.to_path_buf()];
        if let Ok(read) = Registry::read(registry) {
            files.extend(read.manifests());
        }
        Self(
            files
                .into_iter()
                .map(|file| {
                    let stat = fs::metadata(&file).ok().map(|meta| {
                        let modified = meta
                            .modified()
                            .ok()
                            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                            .map_or(0, |since| since.as_nanos());
                        (meta.len(), modified)
                    });
                    (file, stat)
                })
                .collect(),
        )
    }
}

/// What the last load read, and how long to wait before loading again for a failed plugin: loading walks the
/// plugins' folders, so a failure that may never clear by itself is retried less and less often.
pub(super) struct Seen {
    pub(super) stamp: Stamp,
    /// `WATCH_POLL`s between retries.
    pub(super) wait: u32,
    /// `WATCH_POLL`s since the last load.
    pub(super) waited: u32,
}

impl Default for Seen {
    fn default() -> Self {
        Self {
            stamp: Stamp::default(),
            wait: 1,
            waited: 0,
        }
    }
}
