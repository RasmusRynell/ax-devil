//! The frame index of a video: how many frames it has, when each is shown and where decoding can start.
//!
//! It is read from a cache file when a valid one exists. Otherwise it is built by reading the container's packets
//! without decoding, and by decoding every frame only when a packet lacks a timestamp or two share one.

mod scan;
#[cfg(test)]
mod tests;

use crate::fingerprint::Fingerprint;
use crate::index_file::{self, Stored};
use crate::source::{self, Opened};
use foundation::{
    CancelToken, ColorDesc, Interrupt, Keyframes, MediaTime, PixelFormat, Timeline, UserError,
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

/// The index of one video file.
pub struct FrameIndex {
    stored: Stored,
    timeline: Timeline,
    keyframes: Keyframes,
    info: VideoInfo,
}

impl FrameIndex {
    /// Loads the valid cache file under `cache_dir`, or builds the index and saves it there. A source that changed
    /// while it was being read is never saved. `cache_dir = None` never reads or writes a cache.
    ///
    /// `progress` gets values from 0.0 to 1.0 while building and is not called when the cache is used. A build that
    /// falls back to decoding starts counting again from 0.0.
    pub fn load_or_build(
        path: &Path,
        cache_dir: Option<&Path>,
        cancel: &CancelToken,
        progress: &mut dyn FnMut(f32),
    ) -> Result<FrameIndex, Interrupt<UserError>> {
        cancel.check()?;
        let file = File::open(path).map_err(|e| {
            Interrupt::Failed(source::error(
                format!("The video cannot be opened: {e}"),
                path,
            ))
        })?;
        // Taken from the handle before anything is read, so a file replaced from here on shows as a change.
        let fingerprint = Fingerprint::of_handle(&file).map_err(|e| {
            Interrupt::Failed(source::error(
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
        let (scan, pts, keyframes) =
            scan::build(&mut opened, path, fingerprint.size, cancel, progress)?;
        let stored = Stored {
            scan,
            fingerprint,
            stream: opened.stream as u32,
            time_base: opened.time_base,
            first_pts: pts.first().copied().unwrap_or_default(),
            pts,
            keyframes,
        };
        let index = Self::from_stored(stored, &opened, &fingerprint, &info).map_err(|reason| {
            Interrupt::Failed(source::error(
                format!("The video's frame times are unusable: {reason}"),
                path,
            ))
        })?;
        progress(1.0);
        if let Some(cache_file) = &cache_file {
            // A source that changed during the scan may have been read half old, half new.
            if Fingerprint::of_path(path).is_ok_and(|now| now == fingerprint) {
                if let Err(e) = index_file::save(cache_file, &index.stored) {
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

    /// The sequence ids where decoding can start.
    pub fn keyframes(&self) -> &Keyframes {
        &self.keyframes
    }

    /// Size, layout and colour of the frames.
    pub fn info(&self) -> &VideoInfo {
        &self.info
    }

    /// How this index was built.
    pub fn scan(&self) -> ScanKind {
        self.stored.scan
    }

    /// The video stream the index describes.
    pub(crate) fn stream(&self) -> usize {
        self.stored.stream as usize
    }

    /// The pts of frame `sequence_id`, in the stream's time base.
    pub(crate) fn pts(&self, sequence_id: u64) -> Option<i64> {
        self.stored
            .pts
            .get(usize::try_from(sequence_id).ok()?)
            .copied()
    }

    /// The sequence id of the frame with this pts.
    pub(crate) fn sequence_id(&self, pts: i64) -> Option<u64> {
        self.stored
            .pts
            .binary_search(&pts)
            .ok()
            .map(|index| index as u64)
    }

    /// Whether the file at `path` is still the one that was indexed.
    pub(crate) fn describes(&self, path: &Path) -> bool {
        Fingerprint::of_path(path).is_ok_and(|now| now == self.stored.fingerprint)
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
        Ok(Self {
            stored,
            timeline,
            keyframes,
            info: info.clone(),
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
