//! A viewer's handle on its overlays.

use std::{sync::Arc, thread};

use overlay_sync::MatchPolicy;
use playback::{Rank, Want};

use crate::{OverlayEvent, state::Shared};

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
