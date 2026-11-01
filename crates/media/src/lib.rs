//! Video files and image sequences to frames: the frame index, its cache file and the decoder.
//!
//! The index lists every frame's presentation time and where decoding can start; the [`Decoder`] turns a position
//! in it into transport-ready frames. All FFmpeg calls without a safe form are in one private module.

mod convert;
mod decoder;
mod ffi;
mod fingerprint;
mod index;
mod index_file;
mod input;
mod reader;
mod sequence;
mod source;

pub use decoder::Decoder;
pub use index::{FrameIndex, ScanKind, VideoInfo};
pub use input::{ImageSequenceInput, VideoInput};
