//! A viewer's handle on a source: its wants in, its frames out.

use crate::plan::{Rank, nearest, rank};
use crate::source::Source;
use crate::window::send_window;
use crate::{FrameSet, SourceSpec, Want};
use foundation::{FrameFailure, UserError, VideoFrame};
use std::ops::RangeInclusive;
use std::sync::Arc;

/// One thing for a viewer to show.
#[derive(Clone, Debug)]
pub enum Delivery {
    /// A decoded frame.
    Frame(VideoFrame),
    /// A frame that could not be decoded; the others are unaffected.
    Failure(FrameFailure),
    /// The source cannot decode at all, such as a file that was replaced while it was shown. It is the last thing
    /// the session delivers. Its location is the file when the layer that failed knows it.
    Failed(UserError),
}

/// One viewer of a source. Dropping it, or [`Session::close`], leaves the source; the last viewer out closes it.
pub struct Session {
    source: Arc<Source>,
    id: u64,
}

pub(crate) enum Pull {
    Item(Delivery),
    Pending,
    Closed,
}

impl Session {
    pub(crate) fn join(source: &Arc<Source>) -> Option<Session> {
        let id = source.add_session()?;
        Some(Session {
            source: Arc::clone(source),
            id,
        })
    }

    /// The video's timeline, keyframes and frame size, as the source this session joined has them. After
    /// [`Registry::open`](crate::Registry::open) on a file that was already open, these are the open source's, not
    /// the ones passed in.
    pub fn spec(&self) -> &SourceSpec {
        &self.source.spec
    }

    /// Applies a want. Every want updates the sent set, in order, with no coalescing: a frame that leaves the
    /// send window is forgotten and sent again when it re-enters. Only scheduling uses the newest want.
    pub fn want(&self, want: Want) {
        self.source.want(self.id, want);
    }

    /// The next frame or failure to send, or `None` after the session or its source closed, or after it delivered [`Delivery::Failed`]. Frames in the send
    /// window are sent once per residency: the playhead first, then nearest first in the direction of travel,
    /// then the other side. Waits while nothing is ready, so a slow consumer holds back nothing else.
    pub async fn next_delivery(&mut self) -> Option<Delivery> {
        loop {
            let notified = self.source.wake.notified();
            match self.source.pull(self.id) {
                Pull::Item(delivery) => return Some(delivery),
                Pull::Closed => return None,
                Pull::Pending => notified.await,
            }
        }
    }

    /// Leaves the source now.
    pub fn close(self) {}
}

impl Drop for Session {
    fn drop(&mut self) {
        self.source.remove_session(self.id);
    }
}

impl Source {
    /// The best unsent, available frame of the session, marked as sent.
    pub(crate) fn pull(&self, session: u64) -> Pull {
        let mut state = crate::lock(&self.state);
        loop {
            if state.closed {
                return Pull::Closed;
            }
            let fatal = state.fatal.clone();
            let Some(viewer) = state.sessions.get_mut(&session) else {
                return Pull::Closed;
            };
            if let Some(error) = fatal {
                // Told once; the session is over after that.
                return if std::mem::replace(&mut viewer.told, true) {
                    Pull::Closed
                } else {
                    Pull::Item(Delivery::Failed(error))
                };
            }
            let Some(want) = viewer.want else {
                return Pull::Pending;
            };
            let mut available = self.cache.ranges();
            for &id in state.failed.keys() {
                available.insert(id);
            }
            let viewer = &state.sessions[&session];
            for frame in &viewer.parked {
                available.insert(frame.id().sequence_id);
            }
            let window = send_window(&want, self.last());
            let Some(next) = best_unsent(&available, &viewer.sent, &window, &want) else {
                return Pull::Pending;
            };
            let parked = viewer
                .parked
                .iter()
                .find(|frame| frame.id().sequence_id == next);
            let delivery = if let Some(frame) = parked {
                Some(Delivery::Frame(frame.clone()))
            } else if let Some(failure) = state.failed.get(&next) {
                Some(Delivery::Failure(failure.clone()))
            } else {
                self.cache.get(next).map(Delivery::Frame)
            };
            // A frame evicted since the snapshot is simply looked for again.
            let Some(delivery) = delivery else {
                continue;
            };
            let Some(viewer) = state.sessions.get_mut(&session) else {
                return Pull::Closed;
            };
            viewer.sent.insert(next);
            if self.prune_parked(viewer) {
                // A slot is free: the frames this viewer needs, which no one could hold, can be decoded again.
                let viewer = &state.sessions[&session];
                let demanded = self.demanded(viewer);
                for range in &demanded {
                    state.unfit.remove_range(range);
                }
                self.refresh(&mut state);
            }
            return Pull::Item(delivery);
        }
    }
}

fn best_unsent(
    available: &FrameSet,
    sent: &FrameSet,
    window: &RangeInclusive<u64>,
    want: &Want,
) -> Option<u64> {
    let mut best: Option<(Rank, u64)> = None;
    for range in available.ranges() {
        let low = (*range.start()).max(*window.start());
        let high = (*range.end()).min(*window.end());
        if low > high {
            continue;
        }
        for piece in sent.gaps_in(low..=high) {
            let frame = nearest(&piece, want.playhead);
            let key = rank(want.direction, want.playhead, frame);
            if best.is_none_or(|(current, _)| key < current) {
                best = Some((key, frame));
            }
        }
    }
    best.map(|(_, frame)| frame)
}
