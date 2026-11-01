//! What a video is made of: one file, or a numbered run of image files.

use foundation::FrameRate;
use std::path::{Path, PathBuf};

/// Where the frames of a video come from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VideoInput {
    /// A video file.
    File(PathBuf),
    /// A numbered run of image files that plays at a given frame rate.
    ImageSequence(ImageSequenceInput),
}

/// A numbered run of image files. Image `start_number + i` is frame `i`, shown at `frame_rate.time_of(i)`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImageSequenceInput {
    /// The path with one `%d` or `%0Nd` in place of the number, such as `<seq>/img1/%06d.jpg`.
    pub pattern: PathBuf,
    /// The number in the first image's name.
    pub start_number: u32,
    /// Frames per second. The images carry none, so the caller says.
    pub frame_rate: FrameRate,
    /// How many images the run holds, which is how many frames the video has.
    pub frame_count: u64,
}

impl VideoInput {
    /// The path errors about this video name: the file, or the sequence's pattern.
    pub fn location(&self) -> &Path {
        match self {
            Self::File(path) => path,
            Self::ImageSequence(sequence) => &sequence.pattern,
        }
    }
}
