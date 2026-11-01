//! A model of the viewer for the delivery tests: it applies the events in order and the rig drives a `State` the way
//! the viewer's threads and the consumer do, then checks the invariants of `docs/domain/invariants.md` against it.

use std::{
    collections::{BTreeMap, BTreeSet},
    ops::RangeInclusive,
    sync::Arc,
};

use foundation::{CancelToken, MediaTime, Timeline};
use overlay_sync::{MatchPolicy, Sticky, Tolerance};
use playback::{Direction, Priority, Want, rank};
use playlist_resolvers::OverlayRef;
use scene_store::Track;

use super::{Env, SIZE, timeline, want};
use crate::{OverlayEvent, state::State, window::FIRST_NON_SAMPLE};

/// What a viewer would hold after the events it was sent, applied in order.
pub(super) struct Model {
    pub(super) budget: u64,
    pub(super) revision: u32,
    pub(super) policies: BTreeMap<u16, u32>,
    pub(super) held: BTreeMap<u64, u64>,
    pub(super) cells: BTreeMap<(u16, u64), u32>,
    pub(super) window: Option<RangeInclusive<u64>>,
}

impl Model {
    pub(super) fn apply(&mut self, event: &OverlayEvent) {
        match event {
            OverlayEvent::Status {
                status: crate::LaneStatus::Ready(ready),
                ..
            } => {
                self.revision = ready.data_revision;
                self.held.clear();
                self.cells.clear();
            }
            OverlayEvent::Sample(item) => {
                assert_eq!(item.data_revision, self.revision, "Ready precedes items");
                self.held.insert(item.sample, item.item.len() as u64);
                let bytes: u64 = self.held.values().sum();
                assert!(
                    bytes <= self.budget || self.held.len() == 1,
                    "I3: the viewer holds {bytes} of {} bytes in {} samples",
                    self.budget,
                    self.held.len()
                );
            }
            OverlayEvent::Evict {
                data_revision,
                samples,
                ..
            } => {
                if *data_revision == self.revision {
                    for sample in samples {
                        self.held.remove(&u64::from(*sample));
                    }
                }
            }
            OverlayEvent::Window { first, last } => {
                let window = *first..=*last;
                self.cells.retain(|&(_, frame), _| window.contains(&frame));
                self.window = Some(window);
            }
            OverlayEvent::Map(run) => {
                if run.data_revision != self.revision
                    || self.policies.get(&run.lane) != Some(&run.policy_revision)
                {
                    return;
                }
                let window = self.window.as_ref().expect("the window precedes maps");
                assert!(
                    window.contains(run.frames().start()) && window.contains(run.frames().end()),
                    "O3: the run {:?} is outside the window {window:?}",
                    run.frames()
                );
                for (at, &cell) in run.samples.iter().enumerate() {
                    self.cells.insert((run.lane, run.first + at as u64), cell);
                }
            }
            OverlayEvent::Status { .. } | OverlayEvent::Problem { .. } => {}
        }
    }
}

/// A State of one source with `lanes` lanes, driven the way the viewer's threads and the consumer do, with the model
/// as the consumer. The writer drops every `drop_every`th sample or map (never when 0), which is handed back later.
pub(super) struct Rig {
    pub(super) state: State,
    pub(super) track: Arc<Track>,
    pub(super) timeline: Timeline,
    pub(super) model: Model,
    pub(super) dropped: Vec<OverlayEvent>,
    pub(super) drop_every: usize,
    pub(super) seen: usize,
    /// Every item has this many bytes.
    pub(super) item_bytes: u64,
}

impl Rig {
    /// A dense MOT source with a box in each of `frames`, on a timeline of `count` frames.
    pub(super) fn new(
        env: &Env,
        frames: impl IntoIterator<Item = u64>,
        count: u64,
        lanes: usize,
    ) -> Self {
        Self::open(env, env.mot("a.txt", frames), timeline(count), lanes)
    }

    /// A sparse, time-keyed source with a box at each of `frames` of a timeline of `count` frames 40 ms apart, so
    /// that a policy decides which frames a sample belongs to.
    pub(super) fn sparse(
        env: &Env,
        frames: impl IntoIterator<Item = u64>,
        count: u64,
        lanes: usize,
    ) -> Self {
        let text: String = frames
            .into_iter()
            .map(|frame| {
                let ms = frame * 40;
                format!(
                    "<tt:Frame xmlns:tt=\"http://www.onvif.org/ver10/schema\" UtcTime=\"1970-01-01T00:{:02}:{:02}.{:03}Z\">\
                     <tt:Object ObjectId=\"1\"><tt:Appearance><tt:Class><tt:Type Likelihood=\"0.9\">person</tt:Type></tt:Class>\
                     <tt:Shape><tt:BoundingBox left=\"-0.2\" top=\"0.2\" right=\"0.2\" bottom=\"-0.2\"/></tt:Shape>\
                     </tt:Appearance></tt:Object></tt:Frame>\n",
                    ms / 60_000,
                    ms / 1000 % 60,
                    ms % 1000
                )
            })
            .collect();
        let times = (0..count).map(|frame| MediaTime(frame as i64 * 40_000));
        let timeline = Timeline::new(times.collect()).expect("timeline");
        Self::open(
            env,
            env.overlay("a.xml", "ONVIF_XML", &text),
            timeline,
            lanes,
        )
    }

    pub(super) fn open(env: &Env, overlay: OverlayRef, timeline: Timeline, lanes: usize) -> Self {
        let track = env
            .tracks
            .registry
            .open(&overlay, SIZE, &CancelToken::new())
            .expect("opens");
        let mut state = State::new(
            vec![Some(overlay); lanes],
            timeline.last(),
            super::SAMPLE_BUDGET,
        );
        state.finish_load(0, Ok((track.clone(), 100)), &timeline);
        let sizes: BTreeSet<usize> = (0..track.keys().len())
            .map(|sample| track.item(sample).expect("item").len())
            .collect();
        assert_eq!(sizes.len(), 1, "the items have one size: {sizes:?}");
        let item_bytes = sizes.into_iter().next().unwrap_or_default() as u64;
        Self {
            state,
            track,
            timeline,
            model: Model {
                budget: super::SAMPLE_BUDGET,
                revision: 0,
                policies: BTreeMap::new(),
                held: BTreeMap::new(),
                cells: BTreeMap::new(),
                window: None,
            },
            dropped: Vec::new(),
            drop_every: 0,
            seen: 0,
            item_bytes,
        }
    }

    /// The viewer can hold `items` items at once.
    pub(super) fn budget(&mut self, items: u64) {
        self.state.budget = items * self.item_bytes;
        self.model.budget = self.state.budget;
    }

    pub(super) fn want(&mut self, want: Want) {
        self.state.set_want(want, &self.timeline);
    }

    pub(super) fn policy(
        &mut self,
        lane: u16,
        revision: u32,
        matching: MatchPolicy,
        subscribed: bool,
    ) {
        self.model.policies.insert(lane, revision);
        self.model.cells.retain(|&(held, _), _| held != lane);
        self.state.set_policy(
            usize::from(lane),
            revision,
            Some(matching),
            subscribed,
            &self.timeline,
        );
    }

    /// Encodes and sends until nothing more can go; returns what was pulled, in order.
    pub(super) fn pump(&mut self) -> Vec<OverlayEvent> {
        let mut pulled = Vec::new();
        for _ in 0..100_000 {
            while let Some((sample, revision)) = self.state.next_encode(0) {
                let item = self.track.item(sample as usize).expect("item");
                self.state.finish_encode(0, revision, sample, item, None);
            }
            let Some(event) = self.state.pull(None) else {
                return pulled;
            };
            let data = matches!(event, OverlayEvent::Sample(_) | OverlayEvent::Map(_));
            self.seen += usize::from(data);
            if data && self.drop_every > 0 && self.seen.is_multiple_of(self.drop_every) {
                self.dropped.push(event.clone());
            } else {
                self.model.apply(&event);
            }
            pulled.push(event);
        }
        panic!("the state never settles");
    }

    /// Hands back what the writer dropped, all of it, in the order it was dropped.
    pub(super) fn hand_back(&mut self) {
        for event in std::mem::take(&mut self.dropped) {
            self.state.undelivered(&event);
        }
    }

    /// I1 to I4 at quiescence, allowing for what the writer dropped and has not handed back.
    pub(super) fn check(&self) {
        let source = &self.state.sources[0];
        assert!(
            source.evicting.is_empty(),
            "quiescent: every eviction was sent"
        );
        let bytes: u64 = source.sent.values().map(|held| held.bytes).sum();
        assert!(
            bytes <= self.state.budget || source.sent.len() == 1,
            "I3: {bytes} bytes sent"
        );
        let samples: BTreeSet<u64> = self
            .dropped
            .iter()
            .filter_map(|event| match event {
                OverlayEvent::Sample(item) => Some(item.sample),
                _ => None,
            })
            .collect();
        let frames: BTreeSet<(u16, u64)> = self
            .dropped
            .iter()
            .filter_map(|event| match event {
                OverlayEvent::Map(run) => Some(run.frames().map(move |frame| (run.lane, frame))),
                _ => None,
            })
            .flatten()
            .collect();
        for sample in self.model.held.keys() {
            assert!(
                source.sent.contains_key(sample),
                "I1: the viewer holds {sample}, the backend does not"
            );
        }
        for sample in source.sent.keys() {
            assert!(
                self.model.held.contains_key(sample) || samples.contains(sample),
                "I1: the backend counts {sample}, the viewer lacks it"
            );
        }
        assert_eq!(
            self.model.window, self.state.applied,
            "the viewer has the applied window"
        );
        let (want, _) = self.state.window().expect("a want");
        let outranks = |a: u64, b: u64| {
            let urgency = |sample: u64| {
                (
                    rank(want.direction, want.playhead, source.window[&sample]),
                    sample,
                )
            };
            urgency(a) < urgency(b)
        };
        for (index, lane) in self.state.lanes.iter().enumerate() {
            let id = index as u16;
            if lane.window.is_empty() {
                continue;
            }
            for &(held, frame) in self.model.cells.keys() {
                assert!(
                    held != id
                        || self
                            .model
                            .window
                            .as_ref()
                            .is_some_and(|w| w.contains(&frame)),
                    "I4: the cell {frame} is outside the window"
                );
            }
            for frame in self.state.applied.clone().expect("a window") {
                if lane.maps_sent.contains(frame) && !frames.contains(&(id, frame)) {
                    let (cell, _) = lane.window.cell(frame).expect("the lane's window holds it");
                    assert_eq!(
                        self.model.cells.get(&(id, frame)),
                        Some(&cell),
                        "I4: frame {frame} of lane {id}"
                    );
                }
            }
            for &cell in lane
                .window
                .samples
                .iter()
                .filter(|&&cell| cell < FIRST_NON_SAMPLE)
            {
                let sample = u64::from(cell);
                assert!(
                    source.sent.contains_key(&sample)
                        || (source.sent.keys().all(|&sent| outranks(sent, sample))
                            && bytes + self.item_bytes > self.state.budget),
                    "I2: sample {sample} is wanted, not sent and there is room or it outranks what is sent"
                );
            }
        }
    }

    /// The samples the backend counts as sent.
    pub(super) fn sent(&self) -> Vec<u64> {
        self.state.sources[0].sent.keys().copied().collect()
    }
}

/// A sample belongs to its own frame only.
pub(super) fn exact() -> MatchPolicy {
    MatchPolicy {
        tolerance: Tolerance::Exact,
        sticky: Sticky {
            enabled: false,
            timeout: None,
        },
    }
}

/// A sample stays on every later frame.
pub(super) fn sticky() -> MatchPolicy {
    MatchPolicy {
        sticky: Sticky {
            enabled: true,
            timeout: None,
        },
        ..exact()
    }
}

pub(super) fn samples(events: &[OverlayEvent]) -> Vec<u64> {
    events
        .iter()
        .filter_map(|event| match event {
            OverlayEvent::Sample(item) => Some(item.sample),
            _ => None,
        })
        .collect()
}

pub(super) fn evicted(events: &[OverlayEvent]) -> Vec<u32> {
    events
        .iter()
        .flat_map(|event| match event {
            OverlayEvent::Evict { samples, .. } => samples.clone(),
            _ => Vec::new(),
        })
        .collect()
}

pub(super) fn backward(playhead: u64, behind: u32, ahead: u32) -> Want {
    Want {
        direction: Direction::Backward,
        priority: Priority::Normal,
        ..want(playhead, behind, ahead)
    }
}
