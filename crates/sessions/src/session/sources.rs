//! Noticing that the files an item was read from changed.
//!
//! The runner (or whoever added a resolved item) hands the service what each item was read from and what the disk
//! looked like before it was read, so a change made while it was being read still counts. Records are kept per session
//! and item: the same saved workspace open in two windows has the same item ids in both, each with its own baseline.
//! [`Service::check_sources`] looks again and [`poll`] does so once per [`INTERVAL`]; a path counts only once it looked
//! the same on two looks in a row, so a file still being written is held back until it stops. Polling needs no
//! dependency and also sees changes on network shares.

use super::{Service, service::apply, transition::Input};
use crate::{SessionId, common::locked};
use foundation::CancelToken;
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    path::PathBuf,
    sync::Mutex,
    time::Duration,
};
use tokio::{
    sync::watch,
    time::{MissedTickBehavior, interval},
};
use workspace::{ItemId, Snapshot, Watch};

/// How often the files are looked at: a change is reported one to two looks after it was last written.
const INTERVAL: Duration = Duration::from_secs(1);

type Key = (SessionId, ItemId);

/// What one item was read from, and the differences the last look found.
struct Watched {
    active: Option<Watch>,
    /// Only the latest failed attempt is retained alongside the active read set.
    attempt: Option<Watch>,
    /// Differing paths and the snapshots of the active and attempted reads, in that order.
    seen: Option<(BTreeSet<PathBuf>, Vec<Snapshot>)>,
    /// Told apart from an entry recorded since a look began.
    version: u64,
}

#[derive(Default)]
struct Records {
    /// Counts recordings, so a look can tell which entries it knew of.
    version: u64,
    entries: HashMap<Key, Watched>,
}

/// The sources of every item whose Content or failure was recorded.
#[derive(Default)]
pub(super) struct Sources(Mutex<Records>);

impl Sources {
    /// Replaces the current read set and retires any failed attempt.
    pub(super) fn record(&self, session: SessionId, item: ItemId, watch: Watch) {
        self.install(session, item, Some(watch), false);
    }

    /// Retains the active read set with only the latest failed attempt: at most two sets, never a failure history.
    pub(super) fn record_failed(&self, session: SessionId, item: ItemId, watch: Option<Watch>) {
        self.install(session, item, watch, true);
    }

    fn install(&self, session: SessionId, item: ItemId, watch: Option<Watch>, failed: bool) {
        let mut records = locked(&self.0);
        records.version += 1;
        let version = records.version;
        let previous = records.entries.remove(&(session, item));
        let (active, attempt) = if failed {
            (previous.and_then(|watched| watched.active), watch)
        } else {
            (watch, None)
        };
        records.entries.insert(
            (session, item),
            Watched {
                active,
                attempt,
                seen: None,
                version,
            },
        );
    }

    /// The count of recordings so far. Take it before the items that exist, so `look` never forgets a record made
    /// after the items were listed.
    pub(super) fn version(&self) -> u64 {
        locked(&self.0).version
    }

    /// Looks at the files of every recorded item once. Records up to `known` whose item is not in `live` are forgotten.
    /// Returns the items with a path that differs from what was read and looked the same twice; what was seen then
    /// becomes what was read. Blocks on the disk.
    pub(super) fn look(&self, live: &HashSet<Key>, known: u64) -> Vec<(Key, BTreeSet<PathBuf>)> {
        let due: Vec<(Key, u64, Vec<Watch>)> = {
            let mut records = locked(&self.0);
            records
                .entries
                .retain(|key, watched| watched.version > known || live.contains(key));
            records
                .entries
                .iter()
                .map(|(key, watched)| {
                    (
                        *key,
                        watched.version,
                        watched
                            .active
                            .iter()
                            .chain(&watched.attempt)
                            .cloned()
                            .collect(),
                    )
                })
                .collect()
        };
        let never = CancelToken::new();
        let mut changed = Vec::new();
        for (key, version, watches) in due {
            let Ok(now) = watches
                .iter()
                .map(|watch| watch.look(&never))
                .collect::<Result<Vec<_>, _>>()
            else {
                continue;
            };
            let mut records = locked(&self.0);
            let Some(watched) = records
                .entries
                .get_mut(&key)
                .filter(|watched| watched.version == version)
            else {
                continue;
            };
            let differing: BTreeSet<_> = watches
                .iter()
                .zip(&now)
                .flat_map(|(watch, snapshot)| watch.changes(snapshot))
                .collect();
            let steady = match watched.seen.take() {
                Some((before, seen)) => {
                    differing == before
                        && now
                            .iter()
                            .zip(&seen)
                            .all(|(now, before)| now.agrees_at(before, &differing))
                }
                None => false,
            };
            if differing.is_empty() {
                continue;
            }
            if steady {
                for (watch, snapshot) in watched
                    .active
                    .iter_mut()
                    .chain(&mut watched.attempt)
                    .zip(now)
                {
                    watch.accept(snapshot);
                }
                changed.push((key, differing));
            } else {
                watched.seen = Some((differing, now));
            }
        }
        changed
    }
}

/// Maps settled files against active Content. Resolver inputs and failed-attempt-only files remain item-level;
/// Python offers playlist reloads only for considered overlay files of the entry on screen.
pub(super) fn changed_entries(
    workspace: &workspace::store::WorkspaceState,
    item: ItemId,
    paths: &BTreeSet<PathBuf>,
) -> workspace::store::DocCommand {
    use workspace::store::{DocCommand, HiddenRef, Resolution};
    let Some(Resolution::Resolved {
        content: workspace::Content::Playlist(playlist),
        ..
    }) = workspace.resolutions.get(&item)
    else {
        return DocCommand::SourceChanged(item, Vec::new(), true);
    };
    let whole_item = paths.iter().any(|path| {
        !playlist
            .entries
            .iter()
            .any(|entry| entry.overlays.iter().any(|overlay| &overlay.path == path))
    });
    let entries = playlist
        .entries
        .iter()
        .filter(|entry| {
            let hidden = entry.lanes_hidden_at_first();
            entry.overlays.iter().any(|overlay| {
                let lane = HiddenRef::Lane(entry.id.clone(), overlay.label.clone());
                paths.contains(&overlay.path)
                    && !workspace.is_hidden(&lane, hidden.contains(overlay.label.as_str()))
            })
        })
        .map(|entry| entry.id.clone())
        .collect();
    DocCommand::SourceChanged(item, entries, whole_item)
}

impl Service {
    /// Records that `item` of `session` was read from what `watch` says, so a change to it is noticed: a resolved
    /// item that was added without a job records what it was read from here. Replaces what was recorded for the item.
    pub fn record_sources(&self, session: SessionId, item: ItemId, watch: Watch) {
        self.inner.sources.record(session, item, watch);
    }

    /// Looks at the files every item was read from once, and tells each session whose item changed (see
    /// [`DocCommand::SourceChanged`](workspace::store::DocCommand::SourceChanged)). Blocks on the disk.
    pub fn check_sources(&self) {
        // The count comes first: a record made after the items are listed is never forgotten for being missing.
        let known = self.inner.sources.version();
        let live: HashSet<Key> = {
            let sessions = locked(&self.inner.sessions);
            sessions
                .iter()
                .flat_map(|(id, live)| {
                    let items = &live.state.doc.workspace.workspace.items;
                    items.iter().map(|item| (*id, item.id))
                })
                .collect()
        };
        for ((session, item), paths) in self.inner.sources.look(&live, known) {
            self.locked(|sessions, deferred| {
                // A refusal means the session ended: nothing to tell.
                let _ = apply(
                    sessions,
                    session,
                    Input::SourceChanged { item, paths },
                    deferred,
                );
            });
        }
    }
}

/// Looks at the files of every item every [`INTERVAL`] and tells the sessions about the ones that changed, until
/// `stopping` is set.
pub async fn poll(service: Service, mut stopping: watch::Receiver<bool>) {
    let mut tick = interval(INTERVAL);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let looking = service.clone();
                // Looking reads the file system, which must not block the runtime's workers.
                let _ = tokio::task::spawn_blocking(move || looking.check_sources()).await;
            }
            () = async { let _ = stopping.wait_for(|stopping| *stopping).await; } => return,
        }
    }
}

#[cfg(test)]
mod tests;
