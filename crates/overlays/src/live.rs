//! The release step of a live lane: for each frame the live gate lets out, which sample the lane draws, which Sample
//! Items it must send and drop, and which Live Events became visible.
//!
//! A live track has no Timeline and no seekable history, so the lane works one frame at a time on the newest
//! snapshot. It selects under the lane's policy, so a sample that has not come yet never shows early, and sticky and
//! tolerance behave as for a recording. An empty Scene is a sample like any other: selecting it clears the boxes.

use std::{
    collections::{BTreeSet, VecDeque},
    sync::Arc,
};

use foundation::{FrameId, MediaTime, Timeline};
use overlay_sync::{MatchBasis, MatchKind, MatchPolicy, Selection, select};
use scene::{Event, Scene};
use scene_store::{TrackRead, TrackSnapshot};
use tracing::warn;

use crate::{LanePolicy, MapRun, SampleItem, window::NONE_CELL};

/// How many released frames keep a sample on the viewer. A sample none of the last this many frames refers to is
/// evicted, which leaves the viewer time to draw a frame that is still on its way.
pub const LIVE_KEEP_FRAMES: usize = 8;

/// The Scene events of one sample that had not been reported before.
#[derive(Clone, Debug, PartialEq)]
pub struct SampleEvents {
    /// The sample's time on the video clock.
    pub time: MediaTime,
    /// The sample's events, in the Scene's order; never empty.
    pub events: Vec<Event>,
}

/// The Live Events a released frame brings: those of every sample not reported before, up to the frame's time.
#[derive(Clone, Debug, PartialEq)]
pub struct LiveEvents {
    /// The frame that made them visible.
    pub frame: FrameId,
    /// The samples with events, oldest first.
    pub samples: Vec<SampleEvents>,
}

/// What releasing one frame produces for one lane.
#[derive(Clone, Debug, PartialEq)]
pub struct LiveOut {
    /// The frame's Frame Map cell, a run of one frame.
    pub map: MapRun,
    /// The Sample Items the viewer lacks for the cell.
    pub items: Vec<SampleItem>,
    /// Events not reported before; `None` when there are none.
    pub events: Option<LiveEvents>,
    /// The samples (ascending) the viewer may drop: no frame of the last [`LIVE_KEEP_FRAMES`] refers to them.
    pub evict: Vec<u32>,
}

/// One lane over a live track.
pub struct LiveLane {
    track: Arc<dyn TrackRead>,
    source: u16,
    lane: u16,
    policy_revision: u32,
    /// Counts the renumberings of the track's samples seen; the items and maps carry it.
    data_revision: u32,
    /// The track generation `data_revision` stands for, once one is seen.
    generation: Option<u32>,
    /// The samples the viewer holds of this revision.
    sent: BTreeSet<u32>,
    /// The cells of the last released frames, newest last.
    recent: VecDeque<u32>,
    /// The key of the newest sample whose events were reported.
    reported: Option<MediaTime>,
    /// How many of that sample's events were reported. A sample still open when a frame is released at the deadline
    /// can be joined by more events afterwards; those are reported when they arrive.
    reported_events: usize,
    /// The key of the newest sample that matched a frame, within the tolerance. Only it can be retained by sticky: a
    /// sample that never matched one is stale and was discarded before it could persist.
    admitted: Option<MediaTime>,
    serial: u64,
    /// The one-frame clock `select` asks for and never reads when matching by time.
    clock: Timeline,
}

impl LiveLane {
    /// A lane `lane` of the viewer drawing `track` as source `source`, under policy revision `policy_revision`.
    ///
    /// The lane reports the events of samples that arrive after it attaches. Those the track already holds were
    /// reported to the viewers that were there, or are history for this one: a viewer that reconnects keeps the
    /// events it has, so repeating the retained ones would duplicate them. `snapshot` is the view [`release`](Self::release)
    /// would get now, the open sample included, so the events a frame already showed from it count as reported.
    pub fn new(
        track: Arc<dyn TrackRead>,
        snapshot: &TrackSnapshot,
        source: u16,
        lane: u16,
        policy_revision: u32,
    ) -> Self {
        let last = snapshot
            .keys
            .times
            .as_ref()
            .and_then(|times| times.len().checked_sub(1));
        let reported = last.and_then(|last| snapshot.keys.times.as_ref()?.get(last));
        let reported_events = last
            .and_then(|last| track.scene_in(snapshot, last).ok())
            .map_or(0, |scene| scene.events().len());
        Self {
            track,
            source,
            lane,
            policy_revision,
            data_revision: 0,
            generation: None,
            sent: BTreeSet::new(),
            recent: VecDeque::new(),
            reported,
            reported_events,
            admitted: None,
            serial: 0,
            clock: Timeline::new(vec![MediaTime::ZERO]).expect("one frame at zero"),
        }
    }

    /// The revision later maps carry: the lane's policy changed.
    pub fn set_policy_revision(&mut self, revision: u32) {
        self.policy_revision = revision;
    }

    /// The revision the items and maps carry now.
    pub fn data_revision(&self) -> u32 {
        self.data_revision
    }

    /// Whether the viewer holds `sample` of the current revision: it was sent and not evicted since.
    pub fn holds(&self, sample: u32) -> bool {
        self.sent.contains(&sample)
    }

    /// Releases `frame` against `snap`, the newest snapshot of the track, under `policy`. The lane matches by time
    /// whatever the policy's basis. An unsubscribed lane gets no sample and drops all it holds, but still reports
    /// events: the Events list records while the lane is hidden.
    pub fn release(
        &mut self,
        frame: FrameId,
        snap: &TrackSnapshot,
        policy: &LanePolicy,
    ) -> LiveOut {
        if self.generation.is_some_and(|seen| seen != snap.generation) {
            // The samples were numbered anew: the viewer drops the old revision's items, and old cells mean nothing.
            self.data_revision += 1;
            self.sent.clear();
            self.recent.clear();
        }
        self.generation = Some(snap.generation);

        let matching = MatchPolicy {
            basis: Some(MatchBasis::Time),
            ..policy.matching
        };
        let selection = select(
            &snap.keys,
            self.track.coverage(),
            frame,
            &self.clock,
            &matching,
        );
        let (index, kind) = match self.synchronized(frame, snap, &matching, selection) {
            Some((index, kind)) => (Some(index), kind as u8),
            None => (None, 0),
        };
        let events = self.unreported(frame, snap);

        let shown = index
            .filter(|_| policy.subscribed)
            .and_then(|index| u32::try_from(index).ok());
        let mut items = Vec::new();
        if let Some(sample) = shown {
            if self.sent.insert(sample) {
                items.push(self.item(frame, snap, sample));
            }
        } else if !policy.subscribed {
            self.recent.clear();
        }
        self.recent.push_back(shown.unwrap_or(NONE_CELL));
        if self.recent.len() > LIVE_KEEP_FRAMES {
            self.recent.pop_front();
        }
        let evict: Vec<u32> = self
            .sent
            .iter()
            .copied()
            .filter(|sample| !self.recent.contains(sample))
            .collect();
        for sample in &evict {
            self.sent.remove(sample);
        }

        LiveOut {
            map: MapRun {
                lane: self.lane,
                policy_revision: self.policy_revision,
                data_revision: self.data_revision,
                first: frame.sequence_id,
                samples: vec![shown.unwrap_or(NONE_CELL)],
                kinds: vec![if shown.is_some() { kind } else { 0 }],
                frame: frame.sequence_id,
            },
            items,
            events,
            evict,
        }
    }

    /// What `selection` leaves of the policy's pick once live synchronization has its say. A sample within the
    /// tolerance is admitted. Sticky retains only the newest admitted sample, never a newer one that missed the
    /// tolerance and not one that was never admitted, for as long as the sticky timeout allows.
    fn synchronized(
        &mut self,
        frame: FrameId,
        snap: &TrackSnapshot,
        matching: &MatchPolicy,
        selection: Selection,
    ) -> Option<(usize, MatchKind)> {
        let times = snap.keys.times.as_ref()?;
        let Selection::Sample { index, kind } = selection else {
            return None;
        };
        if kind != MatchKind::Retained {
            let key = times.get(index)?;
            self.admitted = self.admitted.max(Some(key));
            return Some((index, kind));
        }
        let admitted = self.admitted.filter(|&key| key <= frame.timestamp)?;
        let age = frame.timestamp.0.saturating_sub(admitted.0);
        if matching
            .sticky
            .timeout
            .is_some_and(|timeout| age > timeout.0)
        {
            return None;
        }
        let at = times.partition_point(|&key| key < admitted);
        (times.get(at) == Some(admitted)).then_some((at, MatchKind::Retained))
    }

    /// The Sample Item of `sample`; one that cannot be read goes empty, so the cell never waits for it.
    fn item(&mut self, frame: FrameId, snap: &TrackSnapshot, sample: u32) -> SampleItem {
        self.serial += 1;
        let time = snap
            .keys
            .times
            .as_ref()
            .and_then(|times| times.get(sample as usize));
        let item = self
            .track
            .item_in(snap, sample as usize)
            .unwrap_or_else(|error| {
                warn!(sample, %error, "a live sample cannot be read");
                Arc::from([])
            });
        SampleItem {
            source: self.source,
            data_revision: self.data_revision,
            sample: u64::from(sample),
            time,
            frame_number: None,
            frame: frame.sequence_id,
            serial: self.serial,
            item,
        }
    }

    /// The events not reported before, of every sample from the last reported one up to the newest at or before the
    /// frame's time, whether or not the policy selects any of them to draw: an event that falls outside the
    /// tolerance, or while the lane is hidden, still reaches the Events list, once. One skipped between two frames
    /// reports as well, and so do events that joined the last reported sample since.
    ///
    /// Independent of the picture: the viewer calls it for a frame it does not show, so the Events list goes on
    /// recording while the lane is paused.
    pub fn unreported(&mut self, frame: FrameId, snap: &TrackSnapshot) -> Option<LiveEvents> {
        let times = snap.keys.times.as_ref()?;
        let from = self
            .reported
            .map_or(0, |reported| times.partition_point(|&key| key < reported));
        let to = times.partition_point(|&key| key <= frame.timestamp);
        if from >= to {
            return None;
        }
        let mut samples = Vec::new();
        let mut last = (None, 0);
        for index in from..to {
            let key = times.get(index);
            let scene = self.scene(snap, index);
            let held = scene.as_ref().map_or(0, |scene| scene.events().len());
            last = (key, held);
            let Some(scene) = scene else {
                continue;
            };
            let seen = if key == self.reported {
                self.reported_events
            } else {
                0
            };
            let events = scene.events().get(seen..).unwrap_or_default();
            if !events.is_empty() {
                samples.push(SampleEvents {
                    time: key.unwrap_or(frame.timestamp),
                    events: events.to_vec(),
                });
            }
        }
        if last.0.is_some() {
            (self.reported, self.reported_events) = last;
        }
        (!samples.is_empty()).then_some(LiveEvents { frame, samples })
    }

    fn scene(&self, snap: &TrackSnapshot, index: usize) -> Option<Arc<Scene>> {
        self.track
            .scene_in(snap, index)
            .inspect_err(|error| warn!(index, %error, "a live sample's events cannot be read"))
            .ok()
    }
}

#[cfg(test)]
mod tests;
