//! The decoded-frame cache: one byte allowance split equally across open sources, under a single mutex.

use crate::FrameSet;
use crate::lock;
use foundation::VideoFrame;
use std::collections::BTreeMap;
use std::ops::RangeInclusive;
use std::sync::{Arc, Mutex};

/// One byte allowance shared by every open source. All sources live behind one mutex, so a resize, a join or a
/// leave is a single step: the shares never add up to more than the allowance, and there is no lock order to get
/// wrong.
#[derive(Debug)]
pub struct CachePool {
    state: Mutex<PoolState>,
}

#[derive(Debug)]
struct PoolState {
    allowance: usize,
    next_id: u64,
    sources: BTreeMap<u64, Slot>,
}

/// One source's frames. `used` never exceeds `share` between calls.
#[derive(Debug, Default)]
struct Slot {
    share: usize,
    used: usize,
    frames: BTreeMap<u64, VideoFrame>,
    present: FrameSet,
    protected: FrameSet,
    protected_bytes: usize,
    center: u64,
}

impl Slot {
    fn distance(&self, id: u64) -> u64 {
        id.abs_diff(self.center)
    }

    fn remove(&mut self, id: u64) {
        if let Some(frame) = self.frames.remove(&id) {
            let bytes = frame.reserved_bytes();
            self.used -= bytes;
            self.present.remove(id);
            if self.protected.contains(id) {
                self.protected_bytes -= bytes;
            }
        }
    }

    fn add(&mut self, frame: VideoFrame) {
        let id = frame.id().sequence_id;
        let bytes = frame.reserved_bytes();
        self.used += bytes;
        self.present.insert(id);
        if self.protected.contains(id) {
            self.protected_bytes += bytes;
        }
        self.frames.insert(id, frame);
    }

    /// The cached frame outside every protected range that is farthest from the centre.
    fn farthest_unprotected(&self) -> Option<u64> {
        let mut from = 0;
        let low = loop {
            let (&id, _) = self.frames.range(from..).next()?;
            match self.protected.range_containing(id) {
                Some(range) => from = range.end().checked_add(1)?,
                None => break id,
            }
        };
        let mut to = u64::MAX;
        let high = loop {
            let (&id, _) = self.frames.range(..=to).next_back()?;
            match self.protected.range_containing(id) {
                Some(range) => to = range.start().checked_sub(1)?,
                None => break id,
            }
        };
        Some(if self.distance(low) >= self.distance(high) {
            low
        } else {
            high
        })
    }

    fn farthest(&self) -> Option<u64> {
        let low = *self.frames.keys().next()?;
        let high = *self.frames.keys().next_back()?;
        Some(if self.distance(low) >= self.distance(high) {
            low
        } else {
            high
        })
    }

    /// Evicts until the frames fit the share: unprotected frames first, protected ones only when nothing else is
    /// left, because a shrinking share is a hard limit.
    fn trim(&mut self) {
        while self.used > self.share {
            let Some(victim) = self.farthest_unprotected().or_else(|| self.farthest()) else {
                break;
            };
            self.remove(victim);
        }
    }
}

impl PoolState {
    /// Splits the allowance equally. Shares that shrink are applied, and their frames evicted, before any share
    /// grows.
    fn rebalance(&mut self) {
        let count = self.sources.len();
        if count == 0 {
            return;
        }
        let share = self.allowance / count;
        for slot in self.sources.values_mut().filter(|slot| slot.share > share) {
            slot.share = share;
            slot.trim();
        }
        for slot in self.sources.values_mut().filter(|slot| slot.share < share) {
            slot.share = share;
        }
    }
}

impl CachePool {
    /// A pool that splits `allowance_bytes` across the sources opened on it.
    pub fn new(allowance_bytes: usize) -> Arc<CachePool> {
        Arc::new(CachePool {
            state: Mutex::new(PoolState {
                allowance: allowance_bytes,
                next_id: 0,
                sources: BTreeMap::new(),
            }),
        })
    }

    /// Applies a new allowance at once: every source's share changes and frames beyond it are evicted.
    pub fn set_allowance(&self, bytes: usize) {
        let mut state = lock(&self.state);
        state.allowance = bytes;
        state.rebalance();
    }

    /// The bytes shared by all sources.
    pub fn allowance(&self) -> usize {
        lock(&self.state).allowance
    }

    /// Number of open sources.
    pub(crate) fn source_count(&self) -> usize {
        lock(&self.state).sources.len()
    }

    /// Opens a source's cache. Every other source's share shrinks to make room, evicting immediately.
    pub(crate) fn add_source(self: &Arc<Self>) -> SourceCache {
        let mut state = lock(&self.state);
        let id = state.next_id;
        state.next_id += 1;
        state.sources.insert(id, Slot::default());
        state.rebalance();
        SourceCache {
            pool: Arc::clone(self),
            id,
        }
    }
}

/// One source's share of a [`CachePool`]. Closing or dropping it returns the share at once.
#[derive(Debug)]
pub(crate) struct SourceCache {
    pool: Arc<CachePool>,
    id: u64,
}

impl SourceCache {
    fn with<R>(&self, default: R, f: impl FnOnce(&mut Slot) -> R) -> R {
        let mut state = lock(&self.pool.state);
        state.sources.get_mut(&self.id).map_or(default, f)
    }

    /// The cached frame, if still held. The frame is shared, so it stays valid after eviction.
    pub fn get(&self, sequence_id: u64) -> Option<VideoFrame> {
        self.with(None, |slot| slot.frames.get(&sequence_id).cloned())
    }

    /// Whether the frame is cached.
    pub fn contains(&self, sequence_id: u64) -> bool {
        self.with(false, |slot| slot.present.contains(sequence_id))
    }

    /// Every cached frame.
    pub fn ranges(&self) -> FrameSet {
        self.with(FrameSet::new(), |slot| slot.present.clone())
    }

    /// Caches `frame`, evicting unprotected frames farthest from the centre first. Returns false when the frame
    /// is larger than the share or cannot fit beside the protected frames (nothing changes), when an unprotected
    /// frame would be the farthest one and so is refused instead of evicting nearer ones, and once the cache is
    /// closed.
    pub fn insert(&self, frame: VideoFrame) -> bool {
        self.with(false, |slot| {
            let id = frame.id().sequence_id;
            let bytes = frame.reserved_bytes();
            if bytes > slot.share {
                return false;
            }
            let protected = slot.protected.contains(id);
            let old = slot.frames.get(&id).map_or(0, VideoFrame::reserved_bytes);
            let others = slot.protected_bytes - if protected { old } else { 0 };
            if others + bytes > slot.share {
                return false;
            }
            slot.remove(id);
            while slot.used + bytes > slot.share {
                let Some(victim) = slot.farthest_unprotected() else {
                    return false;
                };
                if !protected && slot.distance(victim) <= slot.distance(id) {
                    return false;
                }
                slot.remove(victim);
            }
            slot.add(frame);
            true
        })
    }

    /// Frames in `ranges` are never evicted by an insert; the others go farthest from `center` first. Replaces the
    /// previous protection and evicts nothing.
    pub fn protect(&self, center: u64, ranges: &[RangeInclusive<u64>]) {
        self.with((), |slot| {
            slot.center = center;
            let protected = FrameSet::from_ranges(ranges.iter().cloned());
            slot.protected_bytes = protected
                .ranges()
                .iter()
                .flat_map(|range| slot.frames.range(range.clone()))
                .map(|(_, frame)| frame.reserved_bytes())
                .sum();
            slot.protected = protected;
        });
    }

    /// How many sources share the pool, this one included.
    pub(crate) fn sources_open(&self) -> usize {
        self.pool.source_count()
    }

    /// This source's share of the allowance, in bytes.
    pub fn budget_bytes(&self) -> usize {
        self.with(0, |slot| slot.share)
    }

    /// Bytes currently held.
    #[cfg(test)]
    pub(crate) fn used_bytes(&self) -> usize {
        self.with(0, |slot| slot.used)
    }

    /// Frees every frame and returns the share to the other sources. Later inserts fail. Idempotent.
    pub fn close(&self) {
        let mut state = lock(&self.pool.state);
        if state.sources.remove(&self.id).is_some() {
            state.rebalance();
        }
    }
}

impl Drop for SourceCache {
    fn drop(&mut self) {
        self.close();
    }
}
