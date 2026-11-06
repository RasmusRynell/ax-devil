//! The frame index of a video: how many frames it has, when each is shown and where decoding can start.
//!
//! It is read from a cache file when a valid one exists. Otherwise it is built by reading the container's packets
//! without decoding, and by decoding every frame only when a packet lacks a timestamp or two share one.

mod cadence;
mod images;
mod scan;
#[cfg(test)]
mod tests;

pub use cadence::Cadence;

use crate::fingerprint::Fingerprint;
use crate::index_file::{self, Stored};
use crate::input::VideoInput;
use crate::source::{self, Opened};
use foundation::{
    CancelToken, ColorDesc, ErrorCode, Interrupt, Keyframes, MediaTime, PixelFormat, Timeline,
    UserError,
};
use std::fs::File;
use std::path::Path;
use tracing::{debug, warn};

/// How an index was built.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScanKind {
    /// From the container's packets, without decoding.
    Packet,
    /// By decoding every frame, because the packets lacked usable timestamps.
    Decode,
}

/// What a viewer needs to know about the video stream before any frame arrives.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoInfo {
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
    /// The layout frames are delivered in.
    pub format: PixelFormat,
    /// How to convert the delivered samples to RGB.
    pub color: ColorDesc,
}

impl VideoInfo {
    /// Bytes of one delivered frame: the sum of its plane lengths, with no row padding.
    pub fn frame_bytes(&self) -> usize {
        crate::convert::plane_layout(self.format, self.width, self.height)
            .map_or(0, |(_, total)| total)
    }
}

/// How a file's frame times come from its packet timestamps: `(pts - first_pts) * time_base`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PtsTiming {
    /// The stream's time base as numerator and denominator, seconds per tick.
    pub time_base: (i32, i32),
    /// The timestamp of the first frame, in ticks.
    pub first_pts: i64,
}

/// What the timeline was made from.
enum Origin {
    /// A file's packets, as saved in the cache file.
    Packets(Stored),
    /// An image sequence's frame rate: nothing was read but the first image.
    Images {
        /// The video stream of the first image, which the decoder opens its codec from.
        stream: usize,
        /// The first image when it was indexed.
        first: Fingerprint,
    },
}

/// The index of one video: a file, or an image sequence.
pub struct FrameIndex {
    origin: Origin,
    timeline: Timeline,
    cadence: Option<Cadence>,
    keyframes: Keyframes,
    /// How long the container says the last frame lasts, when it does.
    last_duration: Option<MediaTime>,
    info: VideoInfo,
    warnings: Vec<String>,
}

impl FrameIndex {
    /// For a file: loads the valid cache file under `cache_dir`, or builds the index and saves it there. A source
    /// that changed while it was being read is never saved. `cache_dir = None` never reads or writes a cache.
    ///
    /// `progress` gets values from 0.0 to 1.0 while building and is not called when the cache is used. A build that
    /// falls back to decoding starts counting again from 0.0.
    ///
    /// For an image sequence: nothing is scanned and nothing is cached, `cache_dir` and `progress` are not used.
    /// Frame `i` is shown at `frame_rate.time_of(i)` and every frame is a keyframe. The picture size and layout are
    /// those of the first image. The last image is only checked to exist, which [`warnings`](Self::warnings) says
    /// when it does not.
    pub fn load_or_build(
        input: &VideoInput,
        cache_dir: Option<&Path>,
        cancel: &CancelToken,
        progress: &mut dyn FnMut(f32),
    ) -> Result<FrameIndex, Interrupt<UserError>> {
        match input {
            VideoInput::File(path) => Self::of_file(path, cache_dir, cancel, progress),
            VideoInput::ImageSequence(sequence) => Self::of_sequence(sequence, cancel),
        }
    }

    fn of_file(
        path: &Path,
        cache_dir: Option<&Path>,
        cancel: &CancelToken,
        progress: &mut dyn FnMut(f32),
    ) -> Result<FrameIndex, Interrupt<UserError>> {
        cancel.check()?;
        let file = File::open(path).map_err(|e| {
            Interrupt::Failed(source::error(
                ErrorCode::FileUnreadable,
                format!("The video cannot be opened: {e}"),
                path,
            ))
        })?;
        // Taken from the handle before anything is read, so a file replaced from here on shows as a change.
        let fingerprint = Fingerprint::of_handle(&file).map_err(|e| {
            Interrupt::Failed(source::error(
                ErrorCode::FileUnreadable,
                format!("The video cannot be read: {e}"),
                path,
            ))
        })?;
        drop(file);
        let mut opened = source::open(path, cancel)?;
        let info = source::probe(&opened, path).map_err(Interrupt::Failed)?;
        let cache_file = cache_dir.map(|dir| {
            let canonical = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
            index_file::path_for(dir, &canonical)
        });

        if let Some(cache_file) = &cache_file {
            match index_file::load(cache_file) {
                Ok(stored) => match Self::from_stored(stored, &opened, &fingerprint, &info) {
                    Ok(index) => return Ok(index),
                    Err(reason) => {
                        debug!(file = %cache_file.display(), reason, "ignoring the cached frame index")
                    }
                },
                Err(reason) => {
                    debug!(file = %cache_file.display(), reason, "no usable cached frame index")
                }
            }
        }

        cancel.check()?;
        let (scan, scanned) = scan::build(&mut opened, path, fingerprint.size, cancel, progress)?;
        let stored = Stored {
            scan,
            fingerprint,
            stream: opened.stream as u32,
            time_base: opened.time_base,
            first_pts: scanned.pts.first().copied().unwrap_or_default(),
            last_duration: scanned.last_duration,
            pts: scanned.pts,
            keyframes: scanned.keyframes,
        };
        let index = Self::from_stored(stored, &opened, &fingerprint, &info).map_err(|reason| {
            Interrupt::Failed(source::error(
                ErrorCode::IndexingFailed,
                format!("The video's frame times are unusable: {reason}"),
                path,
            ))
        })?;
        progress(1.0);
        if let (Some(cache_file), Origin::Packets(stored)) = (&cache_file, &index.origin) {
            // A source that changed during the scan may have been read half old, half new.
            if Fingerprint::of_path(path).is_ok_and(|now| now == fingerprint) {
                if let Err(e) = index_file::save(cache_file, stored) {
                    warn!(file = %cache_file.display(), error = %e, "could not save the frame index");
                }
            } else {
                debug!(path = %path.display(), "the video changed while it was indexed; not saving");
            }
        }
        Ok(index)
    }

    /// Presentation time of every frame.
    pub fn timeline(&self) -> &Timeline {
        &self.timeline
    }

    /// How evenly the frames are spaced, from their container timestamps; `None` for a one-frame video.
    pub fn cadence(&self) -> Option<&Cadence> {
        self.cadence.as_ref()
    }

    /// How long frame `sequence_id` lasts: the time to the next frame, and for the last frame the duration the
    /// container gives it, else the previous gap. `None` past the end, and for a last frame with no duration and no
    /// earlier frame.
    pub fn duration_of(&self, sequence_id: u64) -> Option<MediaTime> {
        if sequence_id + 1 == self.timeline.len() {
            self.last_duration
                .or_else(|| self.timeline.period_after(sequence_id))
        } else {
            self.timeline.period_after(sequence_id)
        }
    }

    /// The sequence ids where decoding can start.
    pub fn keyframes(&self) -> &Keyframes {
        &self.keyframes
    }

    /// Size, layout and colour of the frames.
    pub fn info(&self) -> &VideoInfo {
        &self.info
    }

    /// How the index of a file was built; `None` for an image sequence, which is not scanned.
    pub fn scan(&self) -> Option<ScanKind> {
        match &self.origin {
            Origin::Packets(stored) => Some(stored.scan),
            Origin::Images { .. } => None,
        }
    }

    /// How the frame times come from the container's timestamps; `None` for an image sequence, which has none.
    pub fn pts_timing(&self) -> Option<PtsTiming> {
        match &self.origin {
            Origin::Packets(stored) => Some(PtsTiming {
                time_base: stored.time_base,
                first_pts: stored.first_pts,
            }),
            Origin::Images { .. } => None,
        }
    }

    /// What is worth telling the user about the video's frames, such as an image that is missing at the end.
    pub fn warnings(&self) -> &[String] {
        &self.warnings
    }

    /// The video stream the index describes.
    pub(crate) fn stream(&self) -> usize {
        match &self.origin {
            Origin::Packets(stored) => stored.stream as usize,
            Origin::Images { stream, .. } => *stream,
        }
    }

    /// The pts of frame `sequence_id`, in the stream's time base. An image sequence's pts is the sequence id.
    pub(crate) fn pts(&self, sequence_id: u64) -> Option<i64> {
        match &self.origin {
            Origin::Packets(stored) => stored.pts.get(usize::try_from(sequence_id).ok()?).copied(),
            Origin::Images { .. } => (sequence_id < self.timeline.len())
                .then(|| i64::try_from(sequence_id).ok())
                .flatten(),
        }
    }

    /// The sequence id of the frame with this pts.
    pub(crate) fn sequence_id(&self, pts: i64) -> Option<u64> {
        match &self.origin {
            Origin::Packets(stored) => stored
                .pts
                .binary_search(&pts)
                .ok()
                .map(|index| index as u64),
            Origin::Images { .. } => u64::try_from(pts)
                .ok()
                .filter(|sequence_id| *sequence_id < self.timeline.len()),
        }
    }

    /// Whether `input` is still what was indexed: the same file, or for a sequence the same first image.
    pub(crate) fn describes(&self, input: &VideoInput) -> bool {
        match (&self.origin, input) {
            (Origin::Packets(stored), VideoInput::File(_)) => {
                Fingerprint::of_input(input).is_ok_and(|now| now == stored.fingerprint)
            }
            (Origin::Images { first, .. }, VideoInput::ImageSequence(_)) => {
                Fingerprint::of_input(input).is_ok_and(|now| now == *first)
            }
            _ => false,
        }
    }

    /// Validates stored values against the file just opened and builds the index from them.
    fn from_stored(
        stored: Stored,
        opened: &Opened,
        fingerprint: &Fingerprint,
        info: &VideoInfo,
    ) -> Result<Self, String> {
        if stored.fingerprint != *fingerprint {
            return Err("the file changed".to_string());
        }
        if stored.stream as usize != opened.stream || stored.time_base != opened.time_base {
            return Err("the video stream differs".to_string());
        }
        let (timeline, keyframes) = times_of(&stored)?;
        let last_duration = MediaTime::from_ticks(
            stored.last_duration,
            i64::from(stored.time_base.0),
            i64::from(stored.time_base.1),
        )
        .filter(|duration| *duration > MediaTime::ZERO);
        Ok(Self {
            cadence: Cadence::of_pts(&stored.pts, stored.time_base),
            origin: Origin::Packets(stored),
            timeline,
            keyframes,
            last_duration,
            info: info.clone(),
            warnings: Vec::new(),
        })
    }
}

/// The timeline and keyframes a stored index describes, or what makes them unusable: frame times must strictly
/// increase, start at 0 and fit, which fails for two frames that share a timestamp.
fn times_of(stored: &Stored) -> Result<(Timeline, Keyframes), String> {
    if stored.pts.first() != Some(&stored.first_pts) {
        return Err("the first timestamp is wrong".to_string());
    }
    if let Some(position) = stored.pts.windows(2).position(|pair| pair[1] <= pair[0]) {
        return Err(format!(
            "frames {position} and {} share a timestamp or are out of order",
            position + 1
        ));
    }
    let (numerator, denominator) = stored.time_base;
    let times = stored
        .pts
        .iter()
        .map(|pts| {
            let ticks = pts.checked_sub(stored.first_pts)?;
            MediaTime::from_ticks(ticks, i64::from(numerator), i64::from(denominator))
        })
        .collect::<Option<Vec<_>>>()
        .ok_or("a frame time does not fit")?;
    let timeline = Timeline::new(times).map_err(|e| format!("{e:?}"))?;
    let keyframes =
        Keyframes::new(stored.keyframes.clone(), timeline.len()).map_err(|e| format!("{e:?}"))?;
    Ok((timeline, keyframes))
}
