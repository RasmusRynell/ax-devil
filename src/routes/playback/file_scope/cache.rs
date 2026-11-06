//! What the lanes' last queries computed, kept so paging and typing a search do not read the file again, within a
//! byte budget over all lanes.

use overlays::FileScopeInput;
use scene_store::{EventMatches, EventPlacement, History, Matches, Placement, Query, Search};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, PoisonError},
};

/// The most memory the lanes' kept queries may take together: two of the largest histories (256 MiB each). A
/// placement takes eight bytes per frame, the events' eight per event and a listing four per object or event, so the history is nearly all of it. A history
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
    pub(super) events: Arc<EventPlacement>,
    pub(super) event_listing: Option<(Search, Arc<EventMatches>)>,
}

impl Cached {
    /// Whether it was made under the revisions `input` has.
    pub(super) fn is_for(&self, input: &FileScopeInput) -> bool {
        (self.data_revision, self.policy_revision) == (input.data_revision, input.policy_revision)
    }

    fn bytes(&self) -> usize {
        let listing = self
            .listing
            .as_ref()
            .map_or(0, |(_, matches)| matches.bytes());
        let events = self
            .event_listing
            .as_ref()
            .map_or(0, |(_, matches)| matches.bytes());
        self.history.bytes() + self.placement.bytes() + listing + self.events.bytes() + events
    }
}

/// A lane that wants its list, and what it kept.
#[derive(Default)]
struct Slot {
    cached: Option<Cached>,
    /// Held while the lane's history and placement are read, so the callers that need them read them once.
    building: Arc<Mutex<()>>,
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

    /// What the lane keeps, which stays kept. `None` when it keeps nothing or wants no list.
    fn peek(&self, lane: u16) -> Option<Cached> {
        self.slots().lanes.get(&lane)?.cached.clone()
    }

    /// The lane's history and placement for the revisions of `input`: the kept ones, else what `build` reads, which
    /// is then kept. A caller that comes while another reads them for the lane waits and uses what that one kept, so
    /// the file is read once.
    pub(super) fn prepared<E>(
        &self,
        lane: u16,
        input: &FileScopeInput,
        build: impl FnOnce() -> Result<Cached, E>,
    ) -> Result<Cached, E> {
        let building = self
            .slots()
            .lanes
            .get(&lane)
            .map(|slot| Arc::clone(&slot.building));
        let _reading = building
            .as_ref()
            .map(|building| building.lock().unwrap_or_else(PoisonError::into_inner));
        match self.peek(lane).filter(|held| held.is_for(input)) {
            Some(held) => Ok(held),
            None => Ok(self.insert(lane, build()?, false)),
        }
    }

    /// Keeps `cached` for the lane unless it was released, then drops the least recently used entries of other lanes
    /// while the budget is exceeded.
    pub(super) fn put(&self, lane: u16, cached: Cached) {
        self.insert(lane, cached, true);
    }

    /// Keeps `cached` as [`Self::put`] does. Unless `replace`, an entry of the same revisions that the lane already
    /// keeps stays, with its listing, and is returned; the entry the lane keeps afterwards is returned otherwise.
    fn insert(&self, lane: u16, cached: Cached, replace: bool) -> Cached {
        let mut slots = self.slots();
        slots.clock += 1;
        let used = slots.clock;
        let Some(slot) = slots.lanes.get_mut(&lane) else {
            return cached;
        };
        if !replace
            && let Some(held) = slot.cached.as_ref().filter(|held| {
                (held.data_revision, held.policy_revision)
                    == (cached.data_revision, cached.policy_revision)
            })
        {
            return held.clone();
        }
        slot.cached = Some(cached.clone());
        slot.used = used;
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
        cached
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
