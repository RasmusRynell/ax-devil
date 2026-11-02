//! A viewer's handle on its overlays.

use std::{sync::Arc, thread};

use foundation::{ErrorCode, Timeline, UserError};
use overlay_sync::MatchPolicy;
use playback::{Rank, Want};

use scene_store::{Ingest, Track, TrackSnapshot};

use crate::{
    LaneSnapshot, OverlayEvent, SourceSnapshot,
    state::{Load, Shared, lane_id},
};

/// What a lane's File scope query reads: the file as the lane's numbering has it now, and how the lane places it.
#[derive(Clone)]
pub struct FileScopeInput {
    /// The decoded file.
    pub track: Arc<Track>,
    /// The file as the lane holds it: its ingest has ended, and it is the numbering of `data_revision`.
    pub snapshot: Arc<TrackSnapshot>,
    /// How the lane matches samples to frames.
    pub matching: MatchPolicy,
    /// The lane's policy revision.
    pub policy_revision: u32,
    /// The lane's source revision.
    pub data_revision: u32,
    /// The video's frames.
    pub timeline: Timeline,
}

/// Why a lane has no File scope.
#[derive(Clone, Debug)]
pub enum FileScopeBlocked {
    /// The lane draws no overlay file.
    Plain,
    /// The file is not loaded yet, or the lane has not yet adopted the end of its ingest.
    Reading,
    /// The file could not be loaded.
    Failed(UserError),
}

/// One viewer's overlays: loads sources and encodes their samples on threads of their own.
///
/// Dropping it closes it, like [`ViewerOverlays::close`].
pub struct ViewerOverlays {
    shared: Arc<Shared>,
    threads: Vec<thread::JoinHandle<()>>,
    sources: Vec<Option<u16>>,
}

impl ViewerOverlays {
    pub(crate) fn new(
        shared: Arc<Shared>,
        threads: Vec<thread::JoinHandle<()>>,
        sources: Vec<Option<u16>>,
    ) -> Self {
        Self {
            shared,
            threads,
            sources,
        }
    }

    /// The source of each lane, `None` for a plain lane. Lanes on the same file share a source.
    pub fn lane_sources(&self) -> &[Option<u16>] {
        &self.sources
    }

    /// Replaces a lane's policy under `revision`: its Frame Map is selected and sent again; the samples the viewer
    /// holds stay until evicted. `matching: None` is a policy the caller refused: the lane keeps its previous
    /// matching but adopts `revision` and `subscribed`. A plain lane, or one past the last lane, is ignored.
    pub fn set_policy(
        &self,
        lane: u16,
        revision: u32,
        matching: Option<MatchPolicy>,
        subscribed: bool,
    ) {
        self.shared.update(|state| {
            state.set_policy(
                usize::from(lane),
                revision,
                matching,
                subscribed,
                &self.shared.timeline,
            );
        });
    }

    /// Moves the data window: the playhead and direction the viewer's gate applied, with `ahead` and `behind` the
    /// data frames. Applied in order: the window is told to the viewer, maps that leave it are forgotten and sent
    /// again when they return, and samples that leave it are evicted.
    pub fn want(&self, window: Want) {
        self.shared
            .update(|state| state.set_want(window, &self.shared.timeline));
    }

    /// Takes back an event the consumer was to deliver but did not (the viewer's authority no longer reached it): what
    /// the viewer still needs of it is offered again by [`next_event`](Self::next_event), within the current window.
    pub fn undelivered(&self, event: &OverlayEvent) {
        self.shared.update(|state| state.undelivered(event));
    }

    /// The next event: statuses and problems first, then the data window and the evictions, then the most urgent
    /// map run or sample, nearest the playhead first and maps before samples. A map run or sample goes only if its
    /// rank is at most `frame_rank`, the rank of the video frame waiting (`None`: no frame waits), so the data wins
    /// ties. Waits while there is nothing; `None` once the viewer is closed.
    pub async fn next_event(&mut self, frame_rank: Option<Rank>) -> Option<OverlayEvent> {
        loop {
            let notified = self.shared.wake.notified();
            let (event, closed) = {
                let mut state = self.shared.lock();
                (state.pull(frame_rank), state.closed)
            };
            match event {
                Some(event) => {
                    // A slot in a source's ready items may be free.
                    self.shared.work.notify_all();
                    return Some(event);
                }
                None if closed => return None,
                None => notified.await,
            }
        }
    }

    /// The track of a sample this viewer holds, the snapshot whose numbering the sample index is of (read it with
    /// [`Track::scene_in`]), and the frame the sample ranks by: only for a ready source of `data_revision` whose
    /// `sample` was sent and is still counted as held, inside the sample window. Anything else is `None`, so the
    /// viewer's authority bounds what an inspection may read.
    pub fn inspect_target(
        &self,
        source: u16,
        data_revision: u32,
        sample: u64,
    ) -> Option<(Arc<Track>, Arc<TrackSnapshot>, u64)> {
        let state = self.shared.lock();
        let slot = state.sources.get(usize::from(source))?;
        let Load::Ready(track, held) = &slot.load else {
            return None;
        };
        if state.closed || slot.data_revision != data_revision || !slot.sent.contains_key(&sample) {
            return None;
        }
        Some((track.clone(), held.clone(), *slot.window.get(&sample)?))
    }

    /// What a File scope query of `lane` reads, taken at one moment under the lock, or why there is none. Only a file
    /// whose ingest the lane has seen end is offered, so a query is of the numbering of the lane's revision.
    /// A lane past the last is `Plain`.
    pub fn file_scope(&self, lane: u16) -> Result<FileScopeInput, FileScopeBlocked> {
        let state = self.shared.lock();
        let lane = state
            .lanes
            .get(usize::from(lane))
            .ok_or(FileScopeBlocked::Plain)?;
        let slot = &state.sources[lane.source.ok_or(FileScopeBlocked::Plain)?];
        let (track, held) = match &slot.load {
            Load::Ready(track, held) => (track, held),
            Load::Loading => return Err(FileScopeBlocked::Reading),
            Load::Failed => {
                return Err(FileScopeBlocked::Failed(UserError::new(
                    ErrorCode::OverlayInvalid,
                    "The overlay file could not be loaded",
                )));
            }
        };
        if held.ingest == Ingest::Reading {
            return Err(FileScopeBlocked::Reading);
        }
        Ok(FileScopeInput {
            track: track.clone(),
            snapshot: held.clone(),
            matching: lane.policy.matching,
            policy_revision: lane.revision,
            data_revision: slot.data_revision,
            timeline: self.shared.timeline.clone(),
        })
    }

    /// Every lane as it is now, taken at one moment: its policy revision and matching, and the loaded file it draws
    /// when it is subscribed. An export freezes this.
    pub fn snapshot(&self) -> Vec<LaneSnapshot> {
        let state = self.shared.lock();
        state
            .lanes
            .iter()
            .map(|lane| LaneSnapshot {
                policy_revision: lane.revision,
                matching: lane.policy.matching,
                source: lane.source.filter(|_| lane.policy.subscribed).and_then(
                    |index| match &state.sources[index].load {
                        Load::Ready(track, held) => Some(SourceSnapshot {
                            track: track.clone(),
                            data_revision: state.sources[index].data_revision,
                            snapshot: held.clone(),
                            source: lane_id(index),
                        }),
                        _ => None,
                    },
                ),
            })
            .collect()
    }

    /// Cancels loads in progress and joins the threads on a thread of their own, so the caller never waits for a
    /// decode to notice.
    pub fn close(self) {}
}

impl Drop for ViewerOverlays {
    fn drop(&mut self) {
        self.shared.close();
        let threads = std::mem::take(&mut self.threads);
        let joiner = thread::Builder::new()
            .name("overlays-join".into())
            .spawn(move || {
                for handle in threads {
                    // A panicked thread already closed the viewer.
                    let _ = handle.join();
                }
            });
        if let Err(error) = joiner {
            // The threads end by themselves after the cancel; they are left to.
            tracing::warn!("cannot join the overlay threads: {error}");
        }
    }
}

#[cfg(test)]
impl ViewerOverlays {
    /// How many encoded items of `source` wait for the consumer.
    pub(crate) fn waiting_items(&self, source: usize) -> usize {
        self.shared.lock().sources[source].ready.len()
    }
}
