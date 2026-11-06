//! Noticing that the files an item was read from changed.
//!
//! The runner records what each item was read from and what the disk looked like before it was read, so a change made
//! while it was being read still counts. [`poll`] looks again once per [`INTERVAL`] and tells the service which items
//! changed; a path counts only once it looked the same on two looks in a row, so a file still being written is held
//! back until it stops. Polling needs no dependency and also sees changes on network shares.

use super::Service;
use crate::common::locked;
use foundation::CancelToken;
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    sync::watch,
    time::{MissedTickBehavior, interval},
};
use workspace::{ItemId, Snapshot, SourcePaths};

/// How often the files are looked at: a change is reported one to two looks after it was last written.
pub const INTERVAL: Duration = Duration::from_secs(1);

/// What one item was read from, what the disk looked like then, and the differences the last look found.
struct Watched {
    paths: SourcePaths,
    baseline: Snapshot,
    /// The paths that differed at the last look, and how they looked.
    seen: Option<(BTreeSet<PathBuf>, Snapshot)>,
    /// Told apart from an entry recorded since the look began.
    version: u64,
}

/// The sources of every item whose Content or failure the runner has recorded.
#[derive(Default)]
pub struct Sources {
    entries: Mutex<(u64, HashMap<ItemId, Watched>)>,
}

impl Sources {
    /// Records that `item` was read from `paths`, which looked like `baseline` before they were read. Replaces what
    /// was recorded for the item before. Nothing to watch records nothing.
    pub fn record(&self, item: ItemId, paths: SourcePaths, baseline: Snapshot) {
        let mut entries = locked(&self.entries);
        entries.0 += 1;
        let version = entries.0;
        if paths.is_empty() {
            entries.1.remove(&item);
        } else {
            let seen = None;
            entries.1.insert(
                item,
                Watched {
                    paths,
                    baseline,
                    seen,
                    version,
                },
            );
        }
    }

    /// Looks at the files of every item in `live` once, forgetting the others, and returns the items with a path that
    /// differs from what was recorded and looked the same twice. What was seen becomes the new baseline. Blocks on
    /// the disk.
    pub fn look(&self, live: &HashSet<ItemId>) -> Vec<ItemId> {
        let due: Vec<(ItemId, u64, SourcePaths)> = {
            let mut entries = locked(&self.entries);
            entries.1.retain(|item, _| live.contains(item));
            entries
                .1
                .iter()
                .map(|(item, watched)| (*item, watched.version, watched.paths.clone()))
                .collect()
        };
        let never = CancelToken::new();
        let mut changed = Vec::new();
        for (item, version, paths) in due {
            let Ok(now) = paths.snapshot(&never) else {
                continue;
            };
            let mut entries = locked(&self.entries);
            let Some(watched) = entries.1.get_mut(&item).filter(|w| w.version == version) else {
                continue;
            };
            let differing = watched.baseline.changes(&now);
            let steady = match watched.seen.take() {
                Some((before, seen)) => differing == before && now.agrees_at(&seen, &differing),
                None => false,
            };
            if differing.is_empty() {
                continue;
            }
            if steady {
                watched.baseline = now;
                changed.push(item);
            } else {
                watched.seen = Some((differing, now));
            }
        }
        changed
    }
}

/// Looks at the files of every item every [`INTERVAL`] and tells `service` about the ones that changed, until
/// `stopping` is set.
pub async fn poll<C: Clone + Send + 'static>(
    service: Service<C>,
    sources: Arc<Sources>,
    mut stopping: watch::Receiver<bool>,
) {
    let mut tick = interval(INTERVAL);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let (live, looking) = (service.item_ids(), Arc::clone(&sources));
                // Looking reads the file system, which must not block the runtime's workers.
                if let Ok(changed) = tokio::task::spawn_blocking(move || looking.look(&live)).await {
                    for item in changed {
                        service.source_changed(item);
                    }
                }
            }
            () = async { let _ = stopping.wait_for(|stopping| *stopping).await; } => return,
        }
    }
}

#[cfg(test)]
mod tests;
