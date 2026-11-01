//! A viewer's handle on its overlays.

use std::{sync::Arc, thread};

use playback::Want;

use crate::{LaneView, OverlayEvent, state::Shared};

/// One viewer's overlays: loads lanes and prepares their frames on a worker.
///
/// Dropping it closes it, like [`ViewerOverlays::close`].
pub struct ViewerOverlays {
    shared: Arc<Shared>,
    threads: Vec<thread::JoinHandle<()>>,
}

impl ViewerOverlays {
    pub(crate) fn new(shared: Arc<Shared>, threads: Vec<thread::JoinHandle<()>>) -> Self {
        Self { shared, threads }
    }

    /// Replaces the lane's view. A new revision resends the lane's whole window; a catalog that fails to compile
    /// keeps the last compiled one and emits [`OverlayEvent::Problem`], the lane staying ready. A lane without an
    /// overlay, or past the last lane, is ignored.
    ///
    /// The catalog compiles on the worker, not the caller.
    pub fn set_view(&self, lane: u16, revision: u32, view: LaneView) {
        self.shared
            .update(|state| state.set_view(usize::from(lane), revision, view));
    }

    /// Moves the window of frames to prepare, with the same windows as the video, applied in order: a frame that
    /// leaves the window is forgotten and prepared again when it returns.
    pub fn want(&self, want: Want) {
        self.shared.update(|state| state.set_want(want));
    }

    /// The next event: statuses and problems first, then overlays nearest the playhead (the playhead, then the
    /// direction of travel, then the other side). Waits while there is nothing; `None` once the viewer is closed.
    pub async fn next_event(&mut self) -> Option<OverlayEvent> {
        loop {
            let notified = self.shared.wake.notified();
            let (event, closed) = {
                let mut state = self.shared.lock();
                (state.pull(), state.closed)
            };
            match event {
                Some(event) => {
                    // A slot in the prepared queue is free.
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
    /// The catalog a lane draws with now.
    pub(crate) fn loaded_catalog(&self, lane: usize) -> Option<catalogs::CatalogId> {
        self.shared.lock().loaded_catalog(lane)
    }
}
