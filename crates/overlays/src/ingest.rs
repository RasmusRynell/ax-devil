//! A source whose track is still being read: what a publication of the ingest changes, and what the lanes are told.
//!
//! A frame is sent once its track has decided it (`data`), so a publication only adds cells; the lanes' status
//! repeats under the same data revision as the ingest advances. When the track renumbers its samples (a new
//! generation) every earlier index is stale, so it begins a new revision like a new track.

use std::{
    sync::Arc,
    time::{Duration, Instant},
};

use foundation::{Timeline, UserError};
use scene_store::{Ingest, Track, TrackSnapshot};

use crate::{
    LaneReady, LaneStatus, OverlayEvent,
    data::LaneWindow,
    state::{Load, State, lane_id},
    window::FIRST_NON_SAMPLE,
};

/// The shortest time between two `Ready` statuses of a source that only report the progress of its ingest.
const READY_EVERY: Duration = Duration::from_millis(500);

impl State {
    /// Whether the ingest of `source` has reached a point where the item size is measured again: its samples were
    /// renumbered, or it ended. `now` is the track's current snapshot.
    pub(crate) fn remeasure(&self, source: usize, now: &TrackSnapshot) -> bool {
        match self.sources.get(source).map(|slot| &slot.load) {
            Some(Load::Ready(_, held)) => {
                now.generation != held.generation
                    || (held.ingest == Ingest::Reading && now.ingest != Ingest::Reading)
            }
            _ => false,
        }
    }

    /// The track of `source` published more samples, or ended. Frames it has decided since get their cells and are
    /// sent once. A renumbering of its samples (a new generation) starts a new data revision like a new track;
    /// otherwise the lanes are told the progress under the same revision as soon as the ingest ended, and at most
    /// every `READY_EVERY` before. `mean` is the item size measured when [`remeasure`](Self::remeasure) said so.
    pub(crate) fn track_changed(&mut self, source: usize, mean: Option<u32>, timeline: &Timeline) {
        if self.closed {
            return;
        }
        let Some(Load::Ready(track, held)) = self.sources.get(source).map(|slot| &slot.load) else {
            return;
        };
        let (track, held) = (Arc::clone(track), Arc::clone(held));
        let now = track.snapshot();
        if Arc::ptr_eq(&now, &held) {
            return;
        }
        let mean = mean.unwrap_or(self.sources[source].mean_sample_bytes);
        if now.generation != held.generation {
            return self.begin_revision(source, track, now, mean, timeline);
        }
        if let Some(error) = too_many(&now) {
            return self.fail(source, &error);
        }
        let ended = now.ingest != held.ingest;
        let slot = &mut self.sources[source];
        slot.load = Load::Ready(track, now);
        slot.mean_sample_bytes = mean;
        self.refresh(timeline);
        if ended || self.sources[source].last_ready.elapsed() >= READY_EVERY {
            self.announce(source);
        }
    }

    /// `track` is a new revision of `source`: the next data revision, a Ready status for its lanes, and every set of
    /// the source and its lanes starting from empty.
    pub(crate) fn begin_revision(
        &mut self,
        source: usize,
        track: Arc<Track>,
        snapshot: Arc<TrackSnapshot>,
        mean_sample_bytes: u32,
        timeline: &Timeline,
    ) {
        if let Some(error) = too_many(&snapshot) {
            return self.fail(source, &error);
        }
        let data_revision = self.next_revision;
        self.next_revision += 1;
        let slot = &mut self.sources[source];
        slot.load = Load::Ready(track, snapshot);
        slot.data_revision = data_revision;
        slot.mean_sample_bytes = mean_sample_bytes;
        // The viewer drops the old revision's samples on Ready, so an Evict still pending is of no use.
        slot.sent.clear();
        slot.evicting.clear();
        slot.ready.clear();
        slot.blocked.clear();
        for lane in self.lanes_of(source).collect::<Vec<_>>() {
            let slot = &mut self.lanes[lane];
            slot.maps_sent.clear();
            slot.window = LaneWindow::default();
            slot.reported.clear();
        }
        self.announce(source);
        self.refresh(timeline);
    }

    /// Tells the lanes of `source` where its load stands now. A status still waiting for the consumer under the same
    /// revision is replaced by this one, so a slow consumer holds one status per lane, not one per publication.
    fn announce(&mut self, source: usize) {
        let slot = &mut self.sources[source];
        slot.last_ready = Instant::now();
        let Load::Ready(track, snapshot) = &slot.load else {
            return;
        };
        let ready = LaneReady {
            clock: snapshot.clock(),
            filter: track.filter().clone(),
            warnings: snapshot.warnings.to_vec(),
            samples: snapshot.keys.len(),
            data_revision: slot.data_revision,
            mean_sample_bytes: slot.mean_sample_bytes,
            ingest: snapshot.ingest.clone(),
        };
        for lane in self.lanes_of(source).collect::<Vec<_>>() {
            let status = LaneStatus::Ready(ready.clone());
            let queued = self.notices.iter_mut().find(|notice| {
                matches!(notice, OverlayEvent::Status { lane: held, status: LaneStatus::Ready(old) }
                    if usize::from(*held) == lane && old.data_revision == ready.data_revision)
            });
            match queued {
                Some(notice) => {
                    *notice = OverlayEvent::Status {
                        lane: lane_id(lane),
                        status,
                    };
                }
                None => self.push_notice(OverlayEvent::Status {
                    lane: lane_id(lane),
                    status,
                }),
            }
        }
    }
}

/// Why a track with this many samples cannot be sent: the Frame Map cell values above them are not sample indices.
fn too_many(snapshot: &TrackSnapshot) -> Option<UserError> {
    (snapshot.keys.len() >= FIRST_NON_SAMPLE as usize).then(|| {
        UserError::new(
            foundation::ErrorCode::OverlayInvalid,
            "The overlay has too many samples",
        )
    })
}
