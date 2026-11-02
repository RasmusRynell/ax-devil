//! Video files and image sequences to frames: the frame index, its cache file and the decoder.
//!
//! The index lists every frame's presentation time and where decoding can start; the [`Decoder`] turns a position
//! in it into transport-ready frames. The [`Encoder`] writes pictures back out as an MP4 or a PNG still. All FFmpeg calls without a safe form are in one private module.

mod convert;
mod decoder;
mod encoder;
mod ffi;
mod fingerprint;
mod index;
mod index_file;
mod input;
mod part;
mod reader;
mod sequence;
mod source;

pub use decoder::Decoder;
pub use encoder::{EncodeFormat, Encoder, Picture, Quality};
pub use index::{FrameIndex, ScanKind, VideoInfo};
pub use input::{ImageSequenceInput, VideoInput};
