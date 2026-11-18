use std::path::PathBuf;

use foundation::FrameRate;

use crate::Settings;

/// Where a video is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VideoRef {
    /// A video file.
    File(PathBuf),
    /// A numbered run of image files.
    ImageSequence(ImageSequence),
}

/// A numbered run of image files that plays as a video.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImageSequence {
    /// The file name pattern, such as `<seq>/img1/%06d.jpg`.
    pub pattern: PathBuf,
    /// The number in the first file's name.
    pub start_number: u32,
    /// Frames per second.
    pub frame_rate: FrameRate,
    /// Image width in pixels, as the dataset declares it; only compared with the decoded size.
    pub width: u32,
    /// Image height in pixels, as the dataset declares it; only compared with the decoded size.
    pub height: u32,
    /// How many images the run holds.
    pub frame_count: u64,
}

/// An overlay file and how to read it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverlayRef {
    /// What the user reads on the lane.
    pub label: String,
    /// The lane's identity within its entry, which workspace settings such as matching are kept under: it is the same
    /// for every entry of a playlist that has this lane (a folder pair's `overlay`, a MOT detector's name). Unique
    /// within an entry.
    pub slot: String,
    /// The overlay file.
    pub path: PathBuf,
    /// The decoder id that reads the file.
    pub handler: String,
    /// Options handed to the decoder, checked against its [`Spec::options`](crate::Spec::options) when it opens.
    pub options: Settings,
    /// Facts the resolver states about the lane, shown in Information; no other behavior depends on them.
    pub metadata: Settings,
}
