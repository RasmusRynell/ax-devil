//! The bounded queues to the app and what happens when the app falls behind.

use tokio::sync::mpsc;
use tokio::sync::mpsc::error::TrySendError;

use crate::live::{AccessUnit, Counters, Gone};

/// The video queue. When it is full, frames are dropped up to the next keyframe, because a decoder cannot use the
/// frames that follow a missing one.
pub(crate) struct VideoOut {
    tx: mpsc::Sender<AccessUnit>,
    dropping: bool,
}

impl VideoOut {
    pub(crate) fn new(tx: mpsc::Sender<AccessUnit>) -> Self {
        Self {
            tx,
            dropping: false,
        }
    }

    /// Queues `unit`, or drops it (and counts it) when the queue is full or a keyframe is awaited.
    pub(crate) fn send(&mut self, unit: AccessUnit, counters: &Counters) -> Result<(), Gone> {
        if self.dropping && !unit.keyframe {
            Counters::add(&counters.video_dropped, 1);
            return Ok(());
        }
        match self.tx.try_send(unit) {
            Ok(()) => self.dropping = false,
            Err(TrySendError::Full(_)) => {
                self.dropping = true;
                Counters::add(&counters.video_dropped, 1);
            }
            Err(TrySendError::Closed(_)) => return Err(Gone),
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::atomic::Ordering;

    use bytes::Bytes;

    use super::*;
    use crate::live::{CodecParams, VideoCodec};

    fn unit(keyframe: bool) -> AccessUnit {
        AccessUnit {
            epoch: 0,
            codec: Arc::new(CodecParams {
                codec: VideoCodec::H264,
                parameter_sets: Bytes::new(),
            }),
            keyframe,
            capture: None,
            data: Bytes::new(),
        }
    }

    #[test]
    fn a_full_video_queue_drops_up_to_the_next_keyframe() {
        let counters = Counters::default();
        let (tx, mut rx) = mpsc::channel(2);
        let mut out = VideoOut::new(tx);
        for keyframe in [true, false, false, true] {
            out.send(unit(keyframe), &counters).unwrap();
        }
        // Two queued; the third overflowed and the keyframe after it was dropped too, as the queue was still full.
        assert_eq!(counters.video_dropped.load(Ordering::Relaxed), 2);
        while rx.try_recv().is_ok() {}
        out.send(unit(false), &counters).unwrap();
        assert!(rx.try_recv().is_err(), "a P frame waits for a keyframe");
        out.send(unit(true), &counters).unwrap();
        assert!(rx.try_recv().unwrap().keyframe);
        assert_eq!(counters.video_dropped.load(Ordering::Relaxed), 3);
    }
}
