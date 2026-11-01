//! Image sequences: the numbered file names and the reader that hands the images to the codec as packets.
//!
//! FFmpeg's `image2` demuxer opens the first image to learn the codec and the picture size (see
//! [`crate::source::open`]), but it does not read the frames: it ends the stream at the first missing file, so a gap
//! would take every later frame with it, and its seek stops at the end it found when it opened. Reading the files
//! here makes every frame independent, every frame a keyframe, and seeking exact.

use crate::input::ImageSequenceInput;
use ffmpeg_next::Packet;
use foundation::CancelToken;
use std::path::PathBuf;

/// A file name pattern with one number in it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Numbering {
    before: String,
    /// Digits the number is padded to with zeros; 0 for plain `%d`.
    width: usize,
    after: String,
}

impl Numbering {
    /// Splits `pattern` at its one `%d` or `%0Nd`.
    pub(crate) fn parse(pattern: &std::path::Path) -> Result<Self, String> {
        let text = pattern
            .to_str()
            .ok_or("the image pattern is not valid text")?;
        let at = text
            .find('%')
            .ok_or("the image pattern has no number in it, such as %06d")?;
        let rest = &text[at + 1..];
        let digits = rest.chars().take_while(char::is_ascii_digit).count();
        let (padding, after) = rest.split_at(digits);
        let Some(after) = after.strip_prefix('d') else {
            return Err("the image pattern must hold %d or %06d for the number".to_string());
        };
        if after.contains('%') {
            return Err("the image pattern has more than one number in it".to_string());
        }
        let width = match padding {
            "" => 0,
            _ if padding.starts_with('0') => padding
                .parse()
                .map_err(|_| "the image pattern's padding is too wide")?,
            _ => return Err("the image pattern must pad with zeros, as in %06d".to_string()),
        };
        Ok(Self {
            before: text[..at].to_string(),
            width,
            after: after.to_string(),
        })
    }

    /// The path of the image numbered `number`.
    pub(crate) fn path(&self, number: u64) -> PathBuf {
        let Self {
            before,
            width,
            after,
        } = self;
        PathBuf::from(format!("{before}{number:0width$}{after}"))
    }
}

/// The images of a sequence, read in order as packets whose pts is the frame's sequence id.
pub(crate) struct ImageFiles {
    numbering: Numbering,
    start_number: u64,
    count: u64,
    next: u64,
}

impl ImageFiles {
    pub(crate) fn new(sequence: &ImageSequenceInput) -> Result<Self, String> {
        Ok(Self {
            numbering: Numbering::parse(&sequence.pattern)?,
            start_number: u64::from(sequence.start_number),
            count: sequence.frame_count,
            next: 0,
        })
    }

    /// The path of the image of frame `sequence_id`.
    pub(crate) fn path_of(&self, sequence_id: u64) -> PathBuf {
        self.numbering.path(self.start_number + sequence_id)
    }

    /// Makes `sequence_id` the next frame read.
    pub(crate) fn seek(&mut self, sequence_id: u64) {
        self.next = sequence_id;
    }

    /// Why frame `sequence_id`'s image cannot be read, when it cannot. Nothing is kept about the images skipped, so a
    /// long run of missing files costs no memory: the file is looked at again when the decoder asks.
    pub(crate) fn failure_of(&self, sequence_id: u64) -> Option<String> {
        let path = self.path_of(sequence_id);
        match std::fs::File::open(&path).and_then(|file| file.metadata()) {
            Ok(meta) if meta.len() == 0 => Some(format!("the image {} is empty", path.display())),
            Ok(_) => None,
            Err(error) => Some(format!(
                "the image {} cannot be read: {error}",
                path.display()
            )),
        }
    }

    /// The next image that can be read, as a packet; `None` after the last frame, or once `cancel` is cancelled. An
    /// image that cannot be read is skipped: its frame then never comes out of the codec.
    pub(crate) fn read(&mut self, cancel: &CancelToken) -> Option<Packet> {
        while self.next < self.count && !cancel.is_cancelled() {
            let sequence_id = self.next;
            self.next += 1;
            match std::fs::read(self.path_of(sequence_id)) {
                Ok(bytes) if !bytes.is_empty() => {
                    let mut packet = Packet::copy(&bytes);
                    let pts = i64::try_from(sequence_id).ok();
                    packet.set_pts(pts);
                    packet.set_dts(pts);
                    return Some(packet);
                }
                _ => {}
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbering_pads_and_rejects_patterns_without_exactly_one_number() {
        let numbering = Numbering::parse("/data/seq/img1/%06d.jpg".as_ref()).unwrap();
        assert_eq!(
            numbering.path(7),
            PathBuf::from("/data/seq/img1/000007.jpg")
        );
        let plain = Numbering::parse("frame_%d.png".as_ref()).unwrap();
        assert_eq!(plain.path(12), PathBuf::from("frame_12.png"));
        for bad in ["img.jpg", "%s.jpg", "%6d.jpg", "%d_%d.jpg", "100%.jpg"] {
            assert!(Numbering::parse(bad.as_ref()).is_err(), "{bad}");
        }
    }

    #[test]
    fn reading_stops_between_images_once_cancelled_and_keeps_nothing_about_missing_ones() {
        let folder = tempfile::tempdir().unwrap();
        let sequence = ImageSequenceInput {
            pattern: folder.path().join("%d.jpg"),
            start_number: 0,
            frame_rate: foundation::FrameRate::parse("30").unwrap(),
            frame_count: u64::MAX,
        };
        let mut files = ImageFiles::new(&sequence).unwrap();
        let cancel = CancelToken::new();
        cancel.cancel();
        assert!(files.read(&cancel).is_none());
        assert_eq!(files.next, 0, "no image was looked at");
        assert!(files.failure_of(7).unwrap().contains("7.jpg"));
    }
}
