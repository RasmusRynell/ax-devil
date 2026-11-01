//! What a viewer sends next: the most urgent map run or sample, against the video frames' rank.

use std::ops::RangeInclusive;

use playback::{Rank, Want, rank};

use crate::{
    MapRun, OverlayEvent,
    state::{Load, State, lane_id},
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
    },
}

impl State {
    /// The next event for the consumer: notices in order, then the most urgent map run or sample whose rank is at
    /// most `frame_rank` (any rank when no frame waits). A map run is sliced from the lane's window now and counted
    /// as sent; a sample leaves `ready`.
    pub(crate) fn pull(&mut self, frame_rank: Option<Rank>) -> Option<OverlayEvent> {
        if let Some(notice) = self.pop_notice() {
            return Some(notice);
        }
        let (want, window) = self.window()?;
        let ((urgency, ..), pick) = self.best(&want, &window)?;
        if frame_rank.is_some_and(|limit| urgency > limit) {
            return None;
        }
        match pick {
            Pick::Sample { source, sample } => {
                let ready = &mut self.sources[source].ready;
                let at = ready.iter().position(|(held, _)| *held == sample)?;
                let (_, item) = ready.remove(at);
                Some(OverlayEvent::Sample(self.sample_item(source, sample, item)))
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

    /// The most urgent thing to send, with its key. A lane offers the first run of each gap of unsent frames: the
    /// part of the gap inside the 256-frame chunk of its frame nearest the playhead. A source offers its most
    /// urgent waiting item.
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
                .filter_map(|(sample, _)| Some((*slot.window.get(sample)?, *sample)))
                .map(|(frame, sample)| (rank(want.direction, want.playhead, frame), sample))
                .min();
            if let Some((urgency, sample)) = urgent {
                offer(
                    (urgency, SAMPLE, lane_id(source), sample),
                    Pick::Sample { source, sample },
                );
            }
        }
        best
    }

    /// The sample of `source` to encode next: the most urgent of its sample window that is not claimed, while fewer
    /// than `READY_PER_SOURCE` items wait.
    pub(crate) fn next_encode(&self, source: usize) -> Option<(u64, u32)> {
        let (want, _) = self.window()?;
        let slot = &self.sources[source];
        if !matches!(slot.load, Load::Ready(_))
            || slot.ready.len() >= crate::state::READY_PER_SOURCE
        {
            return None;
        }
        slot.window
            .iter()
            .filter(|(sample, _)| !slot.claimed.contains(sample))
            .min_by_key(|&(&sample, &frame)| (rank(want.direction, want.playhead, frame), sample))
            .map(|(&sample, _)| (sample, slot.data_revision))
    }
}

/// The run of a gap of unsent frames that goes next: the part inside the 256-frame chunk of the frame nearest the
/// playhead.
fn run_in(gap: &RangeInclusive<u64>, playhead: u64) -> RangeInclusive<u64> {
    let nearest = playhead.clamp(*gap.start(), *gap.end());
    let low = nearest - nearest % RUN_FRAMES;
    (*gap.start()).max(low)..=(*gap.end()).min(low + (RUN_FRAMES - 1))
}
