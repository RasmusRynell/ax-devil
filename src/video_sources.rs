//! Video files as playback sources: the decoder behind the playback crate's trait, and opening a viewer on a file.
//!
//! The playback crate knows frames and windows but not FFmpeg, and the media crate knows FFmpeg but not windows;
//! this module is where they meet.

use foundation::{CancelToken, FrameFailure, Interrupt, UserError, VideoFrame};
use media::{Decoder, FrameIndex};
use playback::{DecoderFactory, Registry, Session, SourceSpec, SpanDecoder};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// A media decoder as a playback span decoder: the playback crate cannot depend on the media crate.
struct MediaDecoder(Decoder);

impl SpanDecoder for MediaDecoder {
    fn position(&self) -> Option<u64> {
        self.0.position()
    }

    fn seek_to_keyframe(&mut self, keyframe: u64) -> Result<(), UserError> {
        self.0.seek_to_keyframe(keyframe)
    }

    fn next_frame(&mut self) -> Result<Option<VideoFrame>, FrameFailure> {
        self.0.next_frame()
    }
}

fn decoder_factory(path: PathBuf, index: Arc<FrameIndex>) -> DecoderFactory {
    Arc::new(move |cancel: &CancelToken, threads: usize| {
        let decoder = Decoder::open(&path, Arc::clone(&index), threads, cancel.clone()).map_err(
            |interrupt| match interrupt {
                Interrupt::Failed(error) => error,
                // The source cancels the token when it closes, and nobody is left to be told.
                Interrupt::Cancelled => {
                    UserError::new("The video was closed", path.display().to_string())
                }
            },
        )?;
        Ok(Box::new(MediaDecoder(decoder)) as Box<dyn SpanDecoder>)
    })
}

/// Opens viewers on video files over one playback registry.
pub struct VideoSources {
    registry: Registry,
    /// Where frame indexes are kept.
    index_dir: PathBuf,
}

impl VideoSources {
    /// Sources in `registry`, with their frame indexes cached under `cache_dir`.
    pub fn new(registry: Registry, cache_dir: &Path) -> Self {
        Self {
            registry,
            index_dir: cache_dir.join("frame-index"),
        }
    }

    /// The sources, for the cache allowance and the shutdown wait.
    pub fn registry(&self) -> &Registry {
        &self.registry
    }

    /// A viewer of the video at `path`: one on the source that is already open, or else a new source once the frame
    /// index is read, which is built (reporting `progress`, 0 to 1) when it is not cached. What the viewer is told
    /// about the video comes from [`Session::spec`], so it describes the source it joined. Blocks: call it on a
    /// blocking thread. Cancelling `cancel` ends an index build early.
    pub fn open(
        &self,
        path: &Path,
        cancel: &CancelToken,
        progress: &mut dyn FnMut(f32),
    ) -> Result<Session, Interrupt<UserError>> {
        if let Some(session) = self.registry.attach(path) {
            return Ok(session);
        }
        let index = Arc::new(FrameIndex::load_or_build(
            path,
            Some(&self.index_dir),
            cancel,
            progress,
        )?);
        let spec = SourceSpec {
            timeline: index.timeline().clone(),
            keyframes: index.keyframes().clone(),
            frame_bytes: index.info().frame_bytes(),
        };
        self.registry
            .open(path, spec, decoder_factory(path.to_path_buf(), index))
            .map_err(Interrupt::Failed)
    }
}
