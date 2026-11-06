//! Video files and image sequences to frames: the frame index, its cache file and the decoder.
//!
//! The index lists every frame's presentation time and where decoding can start; the [`Decoder`] turns a position
//! in it into transport-ready frames; the [`StreamDecoder`] does the same for the units of a live stream, which have no
//! index. The [`Encoder`] writes pictures back out as an MP4 or a PNG still, and [`first_frame`] reads a video's first
//! picture as a JPEG without the index; [`frame_jpeg`] makes one of a decoded frame. All FFmpeg calls without a safe
//! form are in one private module.

mod convert;
mod decoder;
mod encoder;
mod ffi;
mod fingerprint;
mod first_frame;
mod index;
mod index_file;
mod input;
mod jpeg;
mod part;
mod reader;
mod sequence;
mod source;
mod stream;

pub use decoder::Decoder;
pub use encoder::{EncodeFormat, Encoder, Picture, Quality};
pub use first_frame::{MAX_JPEG_BYTES, first_frame, first_frame_identity, whole_jpeg};
pub use index::{Cadence, FrameIndex, PtsTiming, ScanKind, VideoInfo};
pub use index_file::{INDEX_EXTENSION, INDEX_FOLDER};
pub use input::{ImageSequenceInput, VideoInput};
pub use jpeg::{Jpeg, fitted, frame_jpeg};
pub use stream::{StreamCodec, StreamDecoder};
