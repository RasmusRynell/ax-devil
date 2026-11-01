//! The index of an image sequence: made from its frame rate alone.

use super::{FrameIndex, Origin};
use crate::fingerprint::Fingerprint;
use crate::input::{ImageSequenceInput, VideoInput};
use crate::sequence::Numbering;
use crate::source;
use foundation::{CancelToken, Interrupt, Keyframes, Timeline, UserError};

/// The most images one sequence may hold. The index keeps a time and a keyframe for every image (16 bytes), so this
/// bounds it to 80 MB; it is about 46 hours at 30 frames per second, far beyond any dataset sequence.
const MAX_FRAMES: u64 = 5_000_000;

impl FrameIndex {
    /// See [`FrameIndex::load_or_build`]. One open (the first image) and one stat (the last).
    pub(super) fn of_sequence(
        sequence: &ImageSequenceInput,
        cancel: &CancelToken,
    ) -> Result<FrameIndex, Interrupt<UserError>> {
        cancel.check()?;
        let location = sequence.pattern.as_path();
        let fail = |reason: String| Interrupt::Failed(source::error(reason, location));
        if sequence.frame_count == 0 {
            return Err(fail("The image sequence has no images".to_string()));
        }
        if sequence.frame_count > MAX_FRAMES {
            return Err(fail(format!(
                "The image sequence claims {} images but at most {MAX_FRAMES} are supported; check its length, \
                 such as seqLength in a MOT Challenge seqinfo.ini",
                sequence.frame_count
            )));
        }
        let numbering = Numbering::parse(location).map_err(|reason| fail(capitalised(&reason)))?;
        let first_path = numbering.path(u64::from(sequence.start_number));
        let first = Fingerprint::of_path(&first_path)
            .map_err(|e| fail(format!("The first image cannot be read: {e}")))?;
        let opened = source::open_input(&VideoInput::ImageSequence(sequence.clone()), cancel)?;
        let info = source::probe(&opened, location).map_err(Interrupt::Failed)?;

        let times = (0..sequence.frame_count)
            .map(|index| sequence.frame_rate.time_of(index))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| fail("A frame time of the image sequence does not fit".to_string()))?;
        let timeline = Timeline::new(times).map_err(|e| {
            fail(format!(
                "The image sequence's frame times are unusable: {e:?}"
            ))
        })?;
        let keyframes =
            Keyframes::new((0..timeline.len()).collect(), timeline.len()).map_err(|e| {
                fail(format!(
                    "The image sequence's keyframes are unusable: {e:?}"
                ))
            })?;

        let last_path = numbering.path(u64::from(sequence.start_number) + sequence.frame_count - 1);
        let warnings = if last_path.is_file() {
            Vec::new()
        } else {
            vec![format!(
                "The last image of the sequence is missing: {}",
                last_path.display()
            )]
        };
        Ok(FrameIndex {
            origin: Origin::Images {
                stream: opened.stream,
                first,
            },
            timeline,
            keyframes,
            info,
            warnings,
        })
    }
}

/// `reason` starting with a capital, as a message for the user.
fn capitalised(reason: &str) -> String {
    let mut chars = reason.chars();
    chars.next().map_or_else(String::new, |first| {
        first.to_uppercase().chain(chars).collect()
    })
}
