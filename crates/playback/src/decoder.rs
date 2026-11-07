use crate::DECODE_WORKERS;
use foundation::{CancelToken, FrameFailure, Keyframes, Timeline, UserError, VideoFrame};
use std::sync::Arc;

/// One open video decoder, owned by exactly one decode worker.
pub trait SpanDecoder: Send + 'static {
    /// Sequence id the next `next_frame` returns: `Some(0)` after open, `Some(k)` after `seek_to_keyframe(k)`,
    /// `Some(n + 1)` after returning frame `n`, `None` at the end of the stream.
    fn position(&self) -> Option<u64>;

    /// Positions on `keyframe`, a member of the source's keyframes. Output before it is discarded.
    fn seek_to_keyframe(&mut self, keyframe: u64) -> Result<(), UserError>;

    /// The next frame in presentation order, `Ok(None)` at the end of the stream. A failed frame is an `Err`
    /// carrying its sequence id when known; the decoder stays usable and moves past that frame.
    fn next_frame(&mut self) -> Result<Option<VideoFrame>, FrameFailure>;
}

/// Opens one more decoder on the same video. Called on a decode worker, never on a UI or async thread.
///
/// The decoder must stop reading, and report the end of the stream, once the token is cancelled: the source cancels
/// it when it closes, so a decoder blocked in input is interrupted instead of outliving its viewers. The second
/// argument is how many decode threads the decoder may use (see [`decode_threads`]).
///
/// An error here is about the whole source, not a frame: it ends every viewer's session. It carries the file path
/// as its location.
pub type DecoderFactory =
    Arc<dyn Fn(&CancelToken, usize) -> Result<Box<dyn SpanDecoder>, UserError> + Send + Sync>;

/// How many FFmpeg decode threads one decoder gets. The machine's cores are one budget for every decoder of every
/// open source: a source runs `DECODE_WORKERS` decoders, so each decoder gets its share of the cores among all of
/// them, at least 1 and at most 8, since more frame threads add latency and memory without adding speed. A decoder
/// keeps the count it was opened with, so sources opened later share what is left.
pub(crate) fn decode_threads(cores: usize, sources_open: usize) -> usize {
    (cores / (DECODE_WORKERS * sources_open.max(1))).clamp(1, 8)
}

/// What the planner needs to know about a video.
#[derive(Clone, Debug)]
pub struct SourceSpec {
    /// Presentation time of every frame.
    pub timeline: Timeline,
    /// Frames where decoding can start.
    pub keyframes: Keyframes,
    /// Bytes one decoded frame takes in the cache.
    pub frame_bytes: usize,
}
