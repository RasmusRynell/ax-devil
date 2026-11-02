//! What the lanes' last queries computed, kept so paging and typing a search do not read the file again, within a
//! byte budget over all lanes.

use scene_store::{History, Matches, Placement, Query};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, PoisonError},
};

/// The most memory the lanes' kept queries may take together: two of the largest histories (256 MiB each). A
/// placement takes eight bytes per frame and a listing four per object, so the history is nearly all of it. A history
/// two lanes share is counted for each, which only makes the bound safer. One kept query over the budget stays, for
/// the lane that has just used it.
pub(super) const CACHE_BYTES: usize = 512 << 20;

/// What a lane's last query computed, valid for the revisions it names.
#[derive(Clone)]
pub(super) struct Cached {
    pub(super) data_revision: u32,
    pub(super) policy_revision: u32,
    pub(super) history: Arc<History>,
    pub(super) placement: Arc<Placement>,
    pub(super) listing: Option<(Query, Arc<Matches>)>,
}

impl Cached {
    fn bytes(&self) -> usize {
        let listing = self
            .listing
            .as_ref()
            .map_or(0, |(_, matches)| matches.bytes());
        self.history.bytes() + self.placement.bytes() + listing
    }
}

/// A lane that wants its list, and what it kept.
#[derive(Default)]
struct Slot {
    cached: Option<Cached>,
    /// When it was last put, to tell which lane used its entry longest ago.
    used: u64,
}

#[derive(Default)]
struct Slots {
    lanes: HashMap<u16, Slot>,
    clock: u64,
}

/// The kept queries of a viewer's lanes. A lane keeps something only while it wants its list (`open` to `release`), so
/// a query that ends after its lane was released keeps nothing.
pub(super) struct Cache {
    budget: usize,
    slots: Mutex<Slots>,
}

impl Cache {
    pub(super) fn new(budget: usize) -> Self {
        Self {
            budget,
            slots: Mutex::default(),
        }
    }

    fn slots(&self) -> std::sync::MutexGuard<'_, Slots> {
        self.slots.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The lane wants its list: what it computes from now on is kept.
    pub(super) fn open(&self, lane: u16) {
        self.slots().lanes.entry(lane).or_default();
    }

    /// Takes the lane's entry out; it stays out until the query that took it puts it back.
    pub(super) fn take(&self, lane: u16) -> Option<Cached> {
        self.slots().lanes.get_mut(&lane)?.cached.take()
    }

    /// Keeps `cached` for the lane unless it was released, then drops the least recently used entries of other lanes
    /// while the budget is exceeded.
    pub(super) fn put(&self, lane: u16, cached: Cached) {
        let mut slots = self.slots();
        slots.clock += 1;
        let used = slots.clock;
        let Some(slot) = slots.lanes.get_mut(&lane) else {
            return;
        };
        *slot = Slot {
            cached: Some(cached),
            used,
        };
        let mut total: usize = slots
            .lanes
            .values()
            .filter_map(|slot| slot.cached.as_ref())
            .map(Cached::bytes)
            .sum();
        while total > self.budget {
            let oldest = slots
                .lanes
                .iter_mut()
                .filter(|(other, slot)| **other != lane && slot.cached.is_some())
                .min_by_key(|(_, slot)| slot.used);
            let Some(evicted) = oldest.and_then(|(_, slot)| slot.cached.take()) else {
                break;
            };
            total -= evicted.bytes();
        }
    }

    /// The lane no longer wants its list: everything kept for it is dropped.
    pub(super) fn release(&self, lane: u16) {
        self.slots().lanes.remove(&lane);
    }

    /// The lanes that keep something and how many bytes each does.
    #[cfg(test)]
    pub(super) fn held(&self) -> Vec<(u16, usize)> {
        let mut held: Vec<_> = self
            .slots()
            .lanes
            .iter()
            .filter_map(|(lane, slot)| Some((*lane, slot.cached.as_ref()?.bytes())))
            .collect();
        held.sort_unstable();
        held
    }
}
