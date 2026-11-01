//! What a viewer sends next: control messages first, then the most urgent map run or sample, against the video
//! frames' rank.
//!
//! The order is the protocol's (`api_types::playback`): notices, the new data window, the evictions, then data. No
//! map or sample goes while a control message waits, so a sample is never sent before the eviction of its previous
//! send, and a map never before the window it was selected for.

use std::ops::RangeInclusive;

use playback::{Rank, Want, rank};

use crate::{
    MapRun, OverlayEvent,
    state::{Held, Load, State, lane_id},
};

/// Frames of a map chunk; a run never spans two. The `FRAME_MAP_RUN_FRAMES` of the playback wire.
pub(crate) const RUN_FRAMES: u64 = 256;

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
    pub(crate) fn pull(&mut self, frame_rank: Option<Rank>) -> Option<OverlayEvent> {
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
                    self.sample_item(source, sample, serial, item),
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

    /// The most urgent thing to send, with its key. A lane offers the first run of each gap of unsent frames: the
    /// part of the gap inside the 256-frame chunk of its frame nearest the playhead. A source offers its most
    /// urgent waiting item if it fits the budget.
    fn best(&self, want: &Want, window: &RangeInclusive<u64>) -> Option<(Key, Pick)> {
        let mut best: Option<(Key, Pick)> = None;
        let mut offer = |key: Key, pick: Pick| {
            if best.as_ref().is_none_or(|(held, _)| key < *held) {
                best = Some((key, pick));
            }
        };
        for (lane, slot) in self.lanes.iter().enumerate() {
            if slot.window.is_empty() {
                continue;
            }
            for gap in slot.maps_sent.gaps_in(window.clone()) {
                let run = run_in(&gap, want.playhead);
                let frame = want.playhead.clamp(*run.start(), *run.end());
                let urgency = rank(want.direction, want.playhead, frame);
                offer(
                    (urgency, MAP, lane_id(lane), *run.start()),
                    Pick::Map { lane, run },
                );
            }
        }
        for (source, slot) in self.sources.iter().enumerate() {
            let urgent = slot
                .ready
                .iter()
                .filter_map(|(sample, item)| Some((*slot.window.get(sample)?, *sample, item.len())))
                .map(|(frame, sample, len)| {
                    (
                        rank(want.direction, want.playhead, frame),
                        sample,
                        len as u64,
                    )
                })
                .min();
            if let Some((urgency, sample, bytes)) = urgent {
                let key = (urgency, SAMPLE, lane_id(source), sample);
                if let Some(room) = self.room_for(want, &key, bytes) {
                    offer(
                        key,
                        Pick::Sample {
                            source,
                            sample,
                            room,
                        },
                    );
                }
            }
        }
        best
    }

    /// The sent samples to evict, worst first, so that a sample of `bytes` with `key` fits the budget; `None` when
    /// it does not fit. Only samples ranking after it are evicted. It always fits when nothing sent ranks before it,
    /// so a single item larger than the budget still goes.
    fn room_for(&self, want: &Want, key: &Key, bytes: u64) -> Option<Vec<(usize, u64)>> {
        let total: u64 = self
            .sources
            .iter()
            .flat_map(|slot| slot.sent.values())
            .map(|held| held.bytes)
            .sum();
        if total.saturating_add(bytes) <= self.budget {
            return Some(Vec::new());
        }
        let mut sent: Vec<(Key, usize, u64, u64)> = Vec::new();
        for (source, slot) in self.sources.iter().enumerate() {
            for (&sample, held) in &slot.sent {
                let frame = slot.window.get(&sample).copied().unwrap_or(u64::MAX);
                let urgency = rank(want.direction, want.playhead, frame);
                sent.push((
                    (urgency, SAMPLE, lane_id(source), sample),
                    source,
                    sample,
                    held.bytes,
                ));
            }
        }
        sent.sort_unstable_by_key(|held| std::cmp::Reverse(held.0));
        let (mut total, mut room) = (total, Vec::new());
        for (held, source, sample, held_bytes) in &sent {
            if total.saturating_add(bytes) <= self.budget || held < key {
                break;
            }
            total -= held_bytes;
            room.push((*source, *sample));
        }
        (total.saturating_add(bytes) <= self.budget || room.len() == sent.len()).then_some(room)
    }

    /// The sample of `source` to encode next: the most urgent of its sample window that is neither sent nor waiting.
    /// When `READY_PER_SOURCE` items wait, it must outrank the one that ranks last, which is dropped for it: items
    /// that do not fit the budget would otherwise keep a more urgent sample from ever being encoded.
    pub(crate) fn next_encode(&mut self, source: usize) -> Option<(u64, u32)> {
        let (want, _) = self.window()?;
        let slot = &mut self.sources[source];
        if !matches!(slot.load, Load::Ready(_)) {
            return None;
        }
        let urgency =
            |sample: u64, frame: u64| (rank(want.direction, want.playhead, frame), sample);
        let (sample, frame) = slot
            .window
            .iter()
            .filter(|(sample, _)| {
                !slot.sent.contains_key(sample)
                    && !slot.evicting.contains_key(sample)
                    && !slot.ready.iter().any(|(held, _)| held == *sample)
            })
            .min_by_key(|&(&sample, &frame)| urgency(sample, frame))
            .map(|(&sample, &frame)| (sample, frame))?;
        if slot.ready.len() >= crate::state::READY_PER_SOURCE {
            let (worst, _) = slot
                .ready
                .iter()
                .enumerate()
                .filter_map(|(at, (held, _))| Some((at, urgency(*held, *slot.window.get(held)?))))
                .max_by_key(|&(_, urgency)| urgency)?;
            let worse = slot.ready[worst].0;
            if urgency(sample, frame) >= urgency(worse, slot.window.get(&worse).copied()?) {
                return None;
            }
            slot.ready.remove(worst);
        }
        Some((sample, slot.data_revision))
    }
}

/// The run of a gap of unsent frames that goes next: the part inside the 256-frame chunk of the frame nearest the
/// playhead.
fn run_in(gap: &RangeInclusive<u64>, playhead: u64) -> RangeInclusive<u64> {
    let nearest = playhead.clamp(*gap.start(), *gap.end());
    let low = nearest - nearest % RUN_FRAMES;
    (*gap.start()).max(low)..=(*gap.end()).min(low + (RUN_FRAMES - 1))
}
