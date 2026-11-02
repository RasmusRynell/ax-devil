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
use overlay_sync::{Alignment, MatchBasis, MatchPolicy, Tolerance, alignment};
use scene::Coverage;
use scene_decoders::LoadWarning;
use scene_store::{Ingest, Track, TrackSnapshot};

use crate::{
    LaneReady, LaneStatus, OverlayEvent,
    data::LaneWindow,
    state::{Load, Shared, State, lane_id},
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
            self.fit(lane);
        }
        self.announce(source);
        self.refresh(timeline);
    }

    /// Tells the lanes of `source` where its load stands now. A status still waiting for the consumer under the same
    /// revision is replaced by this one, so a slow consumer holds one status per lane, not one per publication.
    pub(crate) fn announce(&mut self, source: usize) {
        self.sources[source].last_ready = Instant::now();
        let Some(ready) = self.ready(source) else {
            return;
        };
        for lane in self.lanes_of(source).collect::<Vec<_>>() {
            self.tell(lane, ready.clone());
        }
    }

    /// The status of `source` once its file is loaded.
    fn ready(&self, source: usize) -> Option<LaneReady> {
        let slot = &self.sources[source];
        let Load::Ready(track, snapshot) = &slot.load else {
            return None;
        };
        Some(LaneReady {
            bases: track.bases(),
            default_basis: track.default_basis(),
            filter: track.filter().clone(),
            warnings: snapshot.warnings.to_vec(),
            samples: snapshot.keys.len(),
            data_revision: slot.data_revision,
            mean_sample_bytes: slot.mean_sample_bytes,
            ingest: snapshot.ingest.clone(),
        })
    }

    /// Queues `ready` for `lane`, with the lane's alignment warning if it is computed for the file and policy the
    /// lane has now.
    fn tell(&mut self, lane: usize, mut ready: LaneReady) {
        let data_revision = ready.data_revision;
        let warning = self.current_job(lane).and_then(|job| {
            let aligned = self.lanes[lane].aligned.as_ref()?;
            aligned.is_for(&job).then(|| aligned.warning.clone())?
        });
        ready.warnings.extend(warning.clone());
        self.lanes[lane].told = warning;
        let status = LaneStatus::Ready(ready);
        let queued = self.notices.iter_mut().find(|notice| {
            matches!(notice, OverlayEvent::Status { lane: held, status: LaneStatus::Ready(old) }
                if usize::from(*held) == lane && old.data_revision == data_revision)
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

    /// What the alignment of `lane` reads now; `None` for a plain lane and while its file is being read.
    fn current_job(&self, lane: usize) -> Option<AlignmentJob> {
        let slot = &self.lanes[lane];
        let Load::Ready(track, snapshot) = &self.sources.get(slot.source?)?.load else {
            return None;
        };
        (snapshot.ingest != Ingest::Reading).then(|| AlignmentJob {
            lane,
            snapshot: Arc::clone(snapshot),
            coverage: track.coverage(),
            matching: slot.policy.matching,
        })
    }

    /// The jobs of the lanes whose alignment warning is not computed for their file and policy as they are now.
    pub(crate) fn stale_alignments(&self) -> Vec<AlignmentJob> {
        (0..self.lanes.len())
            .filter_map(|lane| self.current_job(lane))
            .filter(|job| {
                !self.lanes[job.lane]
                    .aligned
                    .as_ref()
                    .is_some_and(|aligned| aligned.is_for(job))
            })
            .collect()
    }

    /// Takes the warning computed for `job` if the lane's file and policy are still what it read, and tells the lane if it changes what
    /// the lane was last told.
    pub(crate) fn set_alignment(&mut self, job: &AlignmentJob, warning: Option<LoadWarning>) {
        if self.closed
            || self
                .current_job(job.lane)
                .is_none_or(|now| !now.same_inputs(job))
        {
            return;
        }
        let slot = &mut self.lanes[job.lane];
        let told = slot.told != warning;
        slot.aligned = Some(Aligned {
            snapshot: Arc::clone(&job.snapshot),
            basis: job.matching.basis,
            tolerance: job.matching.tolerance,
            warning,
        });
        if told
            && let Some(source) = slot.source
            && let Some(ready) = self.ready(source)
        {
            self.tell(job.lane, ready);
        }
    }
}

impl Shared {
    /// Computes the alignment warnings that are out of date and publishes them. The scan over the video's frames runs
    /// without the state lock, so overlay delivery goes on meanwhile.
    pub(crate) fn align(&self) {
        let jobs = self.lock().stale_alignments();
        for job in jobs {
            if self.cancel.is_cancelled() {
                return;
            }
            let warning = misalignment(&job.run(&self.timeline), &job.matching);
            self.update(|state| state.set_alignment(&job, warning));
        }
    }
}

/// What the alignment of one lane reads, copied out of the viewer state.
pub(crate) struct AlignmentJob {
    lane: usize,
    snapshot: Arc<TrackSnapshot>,
    coverage: Coverage,
    matching: MatchPolicy,
}

impl AlignmentJob {
    fn run(&self, timeline: &Timeline) -> Alignment {
        alignment(&self.snapshot.keys, self.coverage, timeline, &self.matching)
    }

    /// Whether `other` reads the same file as it is now and places it the same way.
    fn same_inputs(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.snapshot, &other.snapshot)
            && self.matching.basis == other.matching.basis
            && self.matching.tolerance == other.matching.tolerance
    }
}

/// The alignment warning of a lane, with the file and policy it was computed for.
pub(crate) struct Aligned {
    snapshot: Arc<TrackSnapshot>,
    basis: Option<MatchBasis>,
    tolerance: Tolerance,
    warning: Option<LoadWarning>,
}

impl Aligned {
    fn is_for(&self, job: &AlignmentJob) -> bool {
        Arc::ptr_eq(&self.snapshot, &job.snapshot)
            && self.basis == job.matching.basis
            && self.tolerance == job.matching.tolerance
    }
}

/// The warning for samples that do not line up with the video's frames: by time, the last one far from the last
/// frame, or too few of the [`attainable`](Alignment::attainable) ones selected by a frame; by frame, too few of them
/// within the video. A file sampled faster than the video leaves samples unselected however well it lines up.
fn misalignment(alignment: &Alignment, policy: &MatchPolicy) -> Option<LoadWarning> {
    let mut reasons = Vec::new();
    if alignment.ends_far()
        && let Some(gap) = alignment.end_gap
    {
        reasons.push(format!(
            "The last sample is at {}, {} {} the last frame at {}",
            seconds(alignment.video_end.0.saturating_add(gap.0)),
            seconds(gap.0.abs()),
            if gap.0 < 0 { "before" } else { "after" },
            seconds(alignment.video_end.0),
        ));
    }
    if !alignment.lines_up() {
        reasons.push(match alignment.basis {
            MatchBasis::Time => {
                let within = match policy.tolerance {
                    Tolerance::Exact => "exactly".to_owned(),
                    Tolerance::PreviousWithin(time) => {
                        format!("exactly or up to {} ms before", time.0 / 1000)
                    }
                };
                format!(
                    "{} of {} samples are on no frame, {within} it",
                    alignment.unmatched(),
                    alignment.samples,
                )
            }
            MatchBasis::Frame => format!(
                "{} of {} records are past the video's last frame (frame {})",
                alignment.unmatched(),
                alignment.samples,
                alignment.last_frame,
            ),
        });
    }
    let consequence = match alignment.basis {
        MatchBasis::Time => {
            "Boxes may be drawn on the wrong frames. The file may count time differently from the video, \
            for example frame number times a fixed frame period instead of the video's own frame times"
        }
        MatchBasis::Frame => "Records past the last frame are not drawn.",
    };
    (!reasons.is_empty()).then(|| LoadWarning {
        problem: "The overlay does not line up with the video's frames".into(),
        reasons,
        consequence: consequence.into(),
        more_reasons: false,
    })
}

/// Microseconds as seconds for the user, such as `41.6 s`.
fn seconds(us: i64) -> String {
    #[expect(
        clippy::cast_precision_loss,
        reason = "a time shown to a tenth of a second needs no exactness"
    )]
    let seconds = us as f64 / 1e6;
    format!("{seconds:.1} s")
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
