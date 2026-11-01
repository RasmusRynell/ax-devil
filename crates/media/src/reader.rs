//! Where a decoder's packets come from: a container, or the image files of a sequence.

use crate::ffi;
use crate::sequence::ImageFiles;
use ffmpeg_next::format::context::Input;
use ffmpeg_next::{Error, Packet};
use foundation::CancelToken;

/// The packet source of one decoder.
pub(crate) enum Reader {
    /// The video stream of an opened container.
    Container { input: Input, stream: usize },
    /// The images of a sequence, one packet each.
    Images(ImageFiles),
}

impl Reader {
    /// Positions on `keyframe`, whose timestamp in the index is `pts`.
    pub(crate) fn seek(&mut self, keyframe: u64, pts: i64) -> Result<(), Error> {
        match self {
            Self::Container { input, stream } => ffi::seek_to_pts(input, *stream, pts),
            Self::Images(files) => {
                files.seek(keyframe);
                Ok(())
            }
        }
    }

    /// The next packet of the video stream, `Ok(None)` for a packet of another stream, and the end of the data or
    /// the reason reading stopped as an error. Reading the images of a sequence also stops when `cancel` is cancelled.
    pub(crate) fn read(&mut self, cancel: &CancelToken) -> Result<Option<Packet>, Error> {
        match self {
            Self::Container { input, stream } => {
                let mut packet = Packet::empty();
                packet.read(input)?;
                Ok((packet.stream() == *stream).then_some(packet))
            }
            Self::Images(files) => files.read(cancel).map(Some).ok_or(Error::Eof),
        }
    }

    /// Why frame `sequence_id` could not be read, for the failure that reports it.
    pub(crate) fn failure_of(&self, sequence_id: u64) -> Option<String> {
        match self {
            Self::Container { .. } => None,
            Self::Images(files) => files.failure_of(sequence_id),
        }
    }
}
