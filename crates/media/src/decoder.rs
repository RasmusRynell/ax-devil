//! The decoder: one file handle and codec context that turns positions of the frame index into frames.

use crate::convert::Converter;
use crate::index::FrameIndex;
use crate::input::VideoInput;
use crate::reader::Reader;
use crate::sequence::ImageFiles;
use crate::source;
use ffmpeg_next::decoder;
use ffmpeg_next::error::EAGAIN;
use ffmpeg_next::frame::Video;
use foundation::{CancelToken, FrameFailure, Interrupt, UserError, VideoFrame};
use std::path::PathBuf;
use std::sync::Arc;
use tracing::{debug, warn};

/// Where the codec is in the stream.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Feed {
    /// More packets can be sent.
    Reading,
    /// The end was signalled; the codec still holds frames.
    EofSent,
    /// The codec has nothing more to give.
    Drained,
}

/// Consecutive codec failures while draining after which it is taken as empty.
const MAX_DRAIN_FAILURES: usize = 64;

/// One decoded frame with its sequence id, before it is due.
type Decoded = (u64, Video);

/// One packet source and codec context. `Send` but not `Sync`: it belongs to exactly one worker.
///
/// Frames come out in presentation order. The codec's output is matched to the index by timestamp, so everything
/// the decoder returns carries the identity the index gave that frame.
pub struct Decoder {
    /// What errors name: the file, or an image sequence's pattern.
    path: PathBuf,
    index: Arc<FrameIndex>,
    reader: Reader,
    codec: decoder::Video,
    converter: Converter,
    position: Option<u64>,
    feed: Feed,
    /// A frame already taken from the codec that is not the next one due.
    held: Option<Decoded>,
    /// Why the codec last failed, to explain the frames it then did not deliver.
    last_error: Option<String>,
    /// FFmpeg polls this while it reads; once cancelled the decoder is at the end of the stream for good.
    cancel: CancelToken,
}

impl Decoder {
    /// Opens `input`, which `index` must describe. `threads` is the number of FFmpeg decode threads; 0 lets FFmpeg
    /// choose. Fails when the file is no longer the one that was indexed. An image sequence is read file by file:
    /// every frame is a keyframe, and an image that cannot be read costs only its own frame.
    ///
    /// `cancel` interrupts opening and every later read and seek that FFmpeg can interrupt (see
    /// [`FrameIndex::load_or_build`]). Opening then returns [`Interrupt::Cancelled`]. A decoder whose token was
    /// cancelled reports the end of the stream: `seek_to_keyframe` succeeds without moving, `position` is `None`
    /// and `next_frame` returns `Ok(None)`.
    pub fn open(
        input: &VideoInput,
        index: Arc<FrameIndex>,
        threads: usize,
        cancel: CancelToken,
    ) -> Result<Decoder, Interrupt<UserError>> {
        let path = input.location();
        if !index.describes(input) {
            return Err(Interrupt::Failed(source::error(
                "The video changed after it was indexed; open it again",
                path,
            )));
        }
        let opened = source::open_input(input, &cancel)?;
        if opened.stream != index.stream() {
            return Err(Interrupt::Failed(source::error(
                "The video's streams changed after it was indexed",
                path,
            )));
        }
        let codec = source::open_decoder(&opened, threads, path).map_err(Interrupt::Failed)?;
        let reader = match input {
            VideoInput::File(_) => Reader::Container {
                input: opened.input,
                stream: opened.stream,
            },
            VideoInput::ImageSequence(sequence) => Reader::Images(
                ImageFiles::new(sequence)
                    .map_err(|reason| Interrupt::Failed(source::error(reason, path)))?,
            ),
        };
        Ok(Decoder {
            path: path.to_path_buf(),
            index,
            reader,
            codec,
            converter: Converter::default(),
            position: Some(0),
            feed: Feed::Reading,
            held: None,
            last_error: None,
            cancel,
        })
    }

    /// The sequence id the next [`next_frame`](Self::next_frame) returns: `Some(0)` after open, `Some(k)` after
    /// `seek_to_keyframe(k)`, `Some(n + 1)` after returning or failing frame `n`, and `None` once the end of the
    /// stream was reported.
    pub fn position(&self) -> Option<u64> {
        self.position
    }

    /// Positions on `keyframe`, which must be one of the index's keyframes, and flushes the codec. Output of
    /// earlier frames, which open-GOP streams produce after a keyframe, is discarded. A rejected request changes
    /// nothing.
    pub fn seek_to_keyframe(&mut self, keyframe: u64) -> Result<(), UserError> {
        if self.cancel.is_cancelled() {
            self.position = None;
            return Ok(());
        }
        let pts = self
            .index
            .keyframes()
            .as_slice()
            .binary_search(&keyframe)
            .ok()
            .and_then(|_| self.index.pts(keyframe))
            .ok_or_else(|| self.error(format!("Frame {keyframe} is not a keyframe")))?;
        self.reader
            .seek(keyframe, pts)
            .map_err(|e| self.error(format!("The video cannot seek to frame {keyframe}: {e}")))?;
        self.codec.flush();
        self.feed = Feed::Reading;
        self.held = None;
        self.last_error = None;
        self.position = Some(keyframe);
        Ok(())
    }

    /// The next frame in presentation order, `Ok(None)` at the end of the stream.
    ///
    /// A frame that cannot be delivered is an `Err` carrying its sequence id, and the position moves past it: a
    /// packet the codec rejects, a frame it never produced (the tail of a truncated file), a damaged frame, or one
    /// that cannot be converted. The decoder stays usable.
    pub fn next_frame(&mut self) -> Result<Option<VideoFrame>, FrameFailure> {
        loop {
            if self.cancel.is_cancelled() {
                self.position = None;
            }
            let Some(next) = self.position else {
                return Ok(None);
            };
            if next >= self.index.timeline().len() {
                self.position = None;
                return Ok(None);
            }
            let candidate = match self.held.take() {
                Some(held) => Some(held),
                None => self.receive(),
            };
            match candidate {
                Some((sequence_id, _)) if sequence_id < next => {}
                Some((sequence_id, frame)) if sequence_id > next => {
                    self.held = Some((sequence_id, frame));
                    return Err(self.failure(next, "the decoder did not produce it"));
                }
                Some((_, frame)) => return self.deliver(next, &frame).map(Some),
                None if self.feed == Feed::Drained => {
                    return Err(self.failure(next, "the file ends early or is damaged"));
                }
                None => self.read_packet(),
            }
        }
    }

    /// Takes the next frame the codec has ready. `None` when it needs more input or is drained.
    fn receive(&mut self) -> Option<Decoded> {
        let mut failures = 0;
        loop {
            let mut frame = Video::empty();
            match self.codec.receive_frame(&mut frame) {
                Ok(()) => {
                    if let Some(sequence_id) =
                        frame.pts().and_then(|pts| self.index.sequence_id(pts))
                    {
                        return Some((sequence_id, frame));
                    }
                    // Decode-only frames and anything the index does not list have no place to go.
                    debug!(path = %self.path.display(), pts = ?frame.pts(), "dropping a frame that is not in the index");
                }
                Err(ffmpeg_next::Error::Eof) => {
                    self.feed = Feed::Drained;
                    return None;
                }
                Err(ffmpeg_next::Error::Other { errno }) if errno == EAGAIN => {
                    // Once the end was signalled, the codec asking for more input means it is empty.
                    if self.feed == Feed::EofSent {
                        self.feed = Feed::Drained;
                    }
                    return None;
                }
                Err(error) => {
                    // The codec dropped a frame it could not decode. Others may still follow it.
                    self.last_error = Some(error.to_string());
                    failures += 1;
                    if self.feed != Feed::EofSent || failures >= MAX_DRAIN_FAILURES {
                        if self.feed == Feed::EofSent {
                            self.feed = Feed::Drained;
                        }
                        return None;
                    }
                }
            }
        }
    }

    /// Sends the next packet of the video stream to the codec, or the end of the stream.
    fn read_packet(&mut self) {
        match self.reader.read(&self.cancel) {
            Ok(Some(packet)) => {
                if let Err(error) = self.codec.send_packet(&packet) {
                    debug!(path = %self.path.display(), %error, "the codec rejected a packet");
                    self.last_error = Some(error.to_string());
                }
            }
            Ok(None) => {}
            Err(error) => {
                if !matches!(error, ffmpeg_next::Error::Eof) && !self.cancel.is_cancelled() {
                    warn!(path = %self.path.display(), %error, "reading the video stopped early");
                    self.last_error = Some(error.to_string());
                }
                // The codec reports an already ended stream as an error, which changes nothing here.
                let _ = self.codec.send_eof();
                self.feed = Feed::EofSent;
            }
        }
    }

    /// Converts frame `next`, which the codec produced, to a transport frame and moves past it.
    fn deliver(&mut self, next: u64, frame: &Video) -> Result<VideoFrame, FrameFailure> {
        self.position = Some(next + 1);
        if frame.is_corrupt() || frame.has_decode_errors() {
            return Err(self.failure(next, "its data is damaged"));
        }
        let (Some(id), keyframe) = (
            self.index.timeline().frame(next),
            self.index
                .keyframes()
                .as_slice()
                .binary_search(&next)
                .is_ok(),
        ) else {
            return Err(self.failure(next, "it is not in the index"));
        };
        match self.converter.convert(frame, id, keyframe) {
            Ok(frame) => {
                self.last_error = None;
                Ok(frame)
            }
            Err(reason) => Err(self.failure(next, &reason)),
        }
    }

    /// Moves past frame `next` and describes why it could not be delivered. The reason is only the cause, for
    /// example "its data is damaged": the viewer says which frame it is about.
    fn failure(&mut self, next: u64, reason: &str) -> FrameFailure {
        self.position = Some(next + 1);
        let detail = self
            .reader
            .failure_of(next)
            .or_else(|| self.last_error.clone())
            .unwrap_or_else(|| reason.to_string());
        FrameFailure {
            sequence_id: next,
            error: self.error(detail),
        }
    }

    fn error(&self, reason: String) -> UserError {
        source::error(reason, &self.path)
    }
}
