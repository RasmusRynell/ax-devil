//! A decoded frame shared by every viewer that is sent it, with its compressed form made at most once.

use bytes::Bytes;
use foundation::{UserError, VideoFrame};
use std::ops::Deref;
use std::sync::Arc;
use tokio::sync::OnceCell;

/// A decoded frame and, once a viewer asks for it, its compressed form. Clones share both, so however many viewers
/// are sent the frame compressed, it is compressed once.
#[derive(Clone, Debug)]
pub struct SharedFrame(Arc<Inner>);

#[derive(Debug)]
struct Inner {
    frame: VideoFrame,
    compressed: OnceCell<Result<Bytes, UserError>>,
}

impl SharedFrame {
    /// The frame, not yet compressed.
    pub fn new(frame: VideoFrame) -> Self {
        Self(Arc::new(Inner {
            frame,
            compressed: OnceCell::new(),
        }))
    }

    /// The frame's compressed form: `make` runs for the first caller only, every concurrent caller waits for that
    /// result, and later callers get it at once, failure included: compressing the same frame again fails the same
    /// way, so retrying would only repeat the work for every viewer. Every caller must pass a `make` that makes the
    /// same bytes, since only one of them runs. Cancelling the caller that runs `make` hands the work to the next.
    pub async fn compressed(
        &self,
        make: impl Future<Output = Result<Bytes, UserError>>,
    ) -> Result<Bytes, UserError> {
        self.0.compressed.get_or_init(|| make).await.clone()
    }
}

impl From<VideoFrame> for SharedFrame {
    fn from(frame: VideoFrame) -> Self {
        Self::new(frame)
    }
}

impl Deref for SharedFrame {
    type Target = VideoFrame;

    fn deref(&self) -> &VideoFrame {
        &self.0.frame
    }
}
