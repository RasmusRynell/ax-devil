//! What a viewer sends next: control messages first, then the most urgent map run or sample, against the video
//! frames' rank.
//!
//! The order is the protocol's (`api_types::playback`): notices, the new data window, the evictions, then data. No
//! map or sample goes while a control message waits, so a sample is never sent before the eviction of its previous
//! send, and a map never before the window it was selected for.

use std::{cell::OnceCell, cmp::Reverse, ops::RangeInclusive};

use foundation::Timeline;
use playback::{Rank, Want, rank};

use crate::{
    MapRun, OverlayEvent, RUN_FRAMES,
    state::{Held, Load, State, lane_id},
};

const MAP: u8 = 0;
const SAMPLE: u8 = 1;

/// What can go next, most urgent first: its rank, maps before samples, then the lane or source, then the position.
type Key = (Rank, u8, u16, u64);

enum Pick {
    Map {
        lane: usize,
        run: RangeInclusive<u64>,
    },
    Sample {
        source: usize,
        sample: u64,
        /// The sent samples, worst first, to evict before it goes so that it fits the budget.
        room: Vec<(usize, u64)>,
    },
}

impl State {
    /// The next event for the consumer: notices in order, the data window, the evictions, then the most urgent map
    /// run or sample whose rank is at most `frame_rank` (any rank when no frame waits); the control messages ignore
    /// the rank. A map run is sliced from the lane's window now and counted as sent. A sample leaves `ready` and is
    /// counted as sent; when it does not fit the budget, the sent samples that rank after it are evicted first, and
    /// it goes on a later pull.
    pub(crate) fn pull(
        &mut self,
        frame_rank: Option<Rank>,
        timeline: &Timeline,
    ) -> Option<OverlayEvent> {
        if let Some(notice) = self.pop_notice() {
            return Some(notice);
        }
        if let Some(window) = self.window_notice.take() {
            return Some(OverlayEvent::Window {
                first: *window.start(),
                last: *window.end(),
            });
        }
        if let Some(evict) = self.pop_evict() {
            return Some(evict);
        }
        let (want, window) = self.window()?;
        let ((urgency, ..), pick) = self.best(&want, &window)?;
        if frame_rank.is_some_and(|limit| urgency > limit) {
            return None;
        }
        match pick {
            Pick::Sample {
                source,
                sample,
                room,
            } => {
                if !room.is_empty() {
                    for (worse, evicted) in room {
                        let slot = &mut self.sources[worse];
                        if let Some(held) = slot.sent.remove(&evicted) {
                            slot.evicting.insert(evicted, held);
                        }
                    }
                    return self.pop_evict();
                }
                let slot = &mut self.sources[source];
                let at = slot.ready.iter().position(|(held, _)| *held == sample)?;
                let (_, item) = slot.ready.remove(at);
                let serial = self.next_serial;
                self.next_serial += 1;
                let bytes = item.len() as u64;
                self.sources[source]
                    .sent
                    .insert(sample, Held { serial, bytes });
                Some(OverlayEvent::Sample(
                    self.sample_item(source, sample, serial, item, timeline),
                ))
            }
            Pick::Map { lane, run } => {
                let slot = &mut self.lanes[lane];
                let (samples, kinds) = slot.window.slice(&run);
                slot.maps_sent.insert_range(run.clone());
                let source = slot.source?;
                Some(OverlayEvent::Map(MapRun {
                    lane: lane_id(lane),
                    policy_revision: slot.revision,
                    data_revision: self.sources[source].data_revision,
                    first: *run.start(),
                    samples,
                    kinds,
                    frame: want.playhead.clamp(*run.start(), *run.end()),
                }))
            }
        }
    }

    /// The eviction of the first source with samples to evict.
    fn pop_evict(&mut self) -> Option<OverlayEvent> {
        let source = self
            .sources
            .iter()
            .position(|slot| !slot.evicting.is_empty())?;
        let slot = &mut self.sources[source];
        let samples = std::mem::take(&mut slot.evicting)
            .into_keys()
            .filter_map(|sample| u32::try_from(sample).ok())
            .collect();
        Some(OverlayEvent::Evict {
            source: lane_id(source),
            data_revision: slot.data_revision,
            samples,
        })
    }

    /// The most urgent thing to send, with its key. A lane offers the first run of each gap of unsent frames it has
    /// decided: the part of the gap inside the 256-frame chunk of its frame nearest the playhead. The sources offer their most
    /// urgent waiting sample that fits the budget; one that does not fit never holds back a less urgent one that
    /// does.
    fn best(&self, want: &Want, window: &RangeInclusive<u64>) -> Option<(Key, Pick)> {
        let mut best: Option<(Key, Pick)> = None;
        for (lane, slot) in self.lanes.iter().enumerate() {
            let Some(decided) = slot.window.frames() else {
                continue;
            };
            let reach =
                (*window.start()).max(*decided.start())..=(*window.end()).min(*decided.end());
            for gap in slot.maps_sent.gaps_in(reach) {
                let run = run_in(&gap, want.playhead);
                let frame = want.playhead.clamp(*run.start(), *run.end());
                let urgency = rank(want.direction, want.playhead, frame);
                let key = (urgency, MAP, lane_id(lane), *run.start());
                if best.as_ref().is_none_or(|(held, _)| key < *held) {
                    best = Some((key, Pick::Map { lane, run }));
                }
            }
        }
        let mut waiting: Vec<(Key, usize, u64, u64)> = Vec::new();
        for (source, slot) in self.sources.iter().enumerate() {
            for (sample, item) in &slot.ready {
                if let Some(&frame) = slot.window.get(sample) {
                    let urgency = rank(want.direction, want.playhead, frame);
                    let key = (urgency, SAMPLE, lane_id(source), *sample);
                    waiting.push((key, source, *sample, item.len() as u64));
                }
            }
        }
        waiting.sort_unstable();
        let sent = Sent::new(self, want);
        for (key, source, sample, bytes) in waiting {
            if best.as_ref().is_some_and(|(held, _)| *held < key) {
                break;
            }
            if let Some(room) = sent.room_for(&key, bytes) {
                let pick = Pick::Sample {
                    source,
                    sample,
                    room,
                };
                best = Some((key, pick));
                break;
            }
        }
        best
    }

    /// The sample of `source` to encode next: the most urgent of its sample window that is neither sent nor waiting
    /// and whose item could fit the budget, which is judged by its size once that is known and by the smallest
    /// item before. An encoded item that no longer fits is dropped for a later sample, its size kept in `blocked`,
    /// so the encoder reaches the samples that do fit (and does not encode the same one again until it fits). When
    /// `READY_PER_SOURCE` items wait, it must outrank the one that ranks last, which is dropped for it.
    pub(crate) fn next_encode(&mut self, source: usize) -> Option<(u64, u32)> {
        let (want, _) = self.window()?;
        let slot = &self.sources[source];
        if !matches!(slot.load, Load::Ready(..)) {
            return None;
        }
        let sent = Sent::new(self, &want);
        let key = |sample: u64, frame: u64| {
            let urgency = rank(want.direction, want.playhead, frame);
            (urgency, SAMPLE, lane_id(source), sample)
        };
        let (mut waiting, mut parked) = (Vec::new(), Vec::new());
        for (sample, item) in &slot.ready {
            let frame = slot.window.get(sample).copied().unwrap_or(u64::MAX);
            let bytes = item.len() as u64;
            if sent.fits(&key(*sample, frame), bytes) {
                waiting.push(key(*sample, frame));
            } else {
                parked.push((*sample, bytes));
            }
        }
        let candidate = slot
            .window
            .iter()
            .filter(|(sample, _)| {
                !slot.sent.contains_key(sample)
                    && !slot.evicting.contains_key(sample)
                    && !slot.ready.iter().any(|(held, _)| held == *sample)
            })
            .map(|(&sample, &frame)| key(sample, frame))
            .filter(|key| sent.fits(key, slot.blocked.get(&key.3).copied().unwrap_or(1)))
            .min();
        let worst = waiting
            .iter()
            .max()
            .filter(|_| waiting.len() >= crate::state::READY_PER_SOURCE);
        let chosen = candidate.filter(|key| worst.is_none_or(|worst| key < worst));
        let dropped = chosen.and(worst).map(|worst| worst.3);
        let revision = slot.data_revision;
        let slot = &mut self.sources[source];
        for (sample, bytes) in parked {
            slot.ready.retain(|(held, _)| *held != sample);
            slot.blocked.insert(sample, bytes);
        }
        if let Some(sample) = dropped {
            slot.ready.retain(|(held, _)| *held != sample);
        }
        chosen.map(|key| (key.3, revision))
    }
}

/// The samples all sources have sent, against the byte budget.
struct Sent<'a> {
    state: &'a State,
    want: &'a Want,
    /// The bytes of all sent samples.
    total: u64,
    /// The sent samples, worst first; built the first time something does not fit outright.
    worst_first: OnceCell<Vec<Ranked>>,
}

/// A sent sample, for choosing which to evict.
struct Ranked {
    key: Key,
    source: usize,
    sample: u64,
    /// The bytes freed by evicting this sample and every one before it.
    freed: u64,
}

impl<'a> Sent<'a> {
    fn new(state: &'a State, want: &'a Want) -> Self {
        let total = state
            .sources
            .iter()
            .flat_map(|slot| slot.sent.values())
            .map(|held| held.bytes)
            .sum();
        Self {
            state,
            want,
            total,
            worst_first: OnceCell::new(),
        }
    }

    fn worst_first(&self) -> &[Ranked] {
        self.worst_first.get_or_init(|| {
            let mut sent = Vec::new();
            for (source, slot) in self.state.sources.iter().enumerate() {
                for (&sample, held) in &slot.sent {
                    let frame = slot.window.get(&sample).copied().unwrap_or(u64::MAX);
                    let urgency = rank(self.want.direction, self.want.playhead, frame);
                    let key = (urgency, SAMPLE, lane_id(source), sample);
                    sent.push((key, source, sample, held.bytes));
                }
            }
            sent.sort_unstable_by_key(|held| Reverse(held.0));
            let mut freed = 0;
            sent.into_iter()
                .map(|(key, source, sample, bytes)| {
                    freed += bytes;
                    Ranked {
                        key,
                        source,
                        sample,
                        freed,
                    }
                })
                .collect()
        })
    }

    /// How many of the worst sent samples to evict so that a sample of `bytes` with `key` fits the budget; `None`
    /// when it does not fit. Only samples ranking after it are evicted. It always fits when nothing sent ranks
    /// before it, so a single item larger than the budget still goes.
    fn evictions(&self, key: &Key, bytes: u64) -> Option<usize> {
        let budget = self.state.budget;
        if self.total.saturating_add(bytes) <= budget {
            return Some(0);
        }
        let sent = self.worst_first();
        let worse = sent.partition_point(|held| held.key > *key);
        let enough = sent[..worse]
            .partition_point(|held| (self.total - held.freed).saturating_add(bytes) > budget);
        if enough < worse {
            Some(enough + 1)
        } else {
            (worse == sent.len()).then_some(worse)
        }
    }

    fn fits(&self, key: &Key, bytes: u64) -> bool {
        self.evictions(key, bytes).is_some()
    }

    /// The sent samples to evict, worst first, so that a sample of `bytes` with `key` fits the budget; `None` when
    /// it does not fit.
    fn room_for(&self, key: &Key, bytes: u64) -> Option<Vec<(usize, u64)>> {
        let count = self.evictions(key, bytes)?;
        let room = self
            .worst_first
            .get()
            .map_or(&[][..], |sent| &sent[..count]);
        Some(room.iter().map(|held| (held.source, held.sample)).collect())
    }
}

/// The run of a gap of unsent frames that goes next: the part inside the 256-frame chunk of the frame nearest the
/// playhead.
fn run_in(gap: &RangeInclusive<u64>, playhead: u64) -> RangeInclusive<u64> {
    let nearest = playhead.clamp(*gap.start(), *gap.end());
    let chunk = u64::from(RUN_FRAMES);
    let low = nearest - nearest % chunk;
    (*gap.start()).max(low)..=(*gap.end()).min(low + (chunk - 1))
}
