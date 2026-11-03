//! Image sequences as videos: `fixtures/video/seq` holds 12 JPEGs numbered from 1 whose image k is flat with luma
//! 7 * (k - 1) (`generate.sh`), so a decoded frame says which image it came from.

mod common;

use common::{assert_explained, expected_luma, flat_luma, index_of_input, open_input, read_all};
use foundation::{CancelToken, FrameRate, HostPath, Interrupt};
use media::{FrameIndex, ImageSequenceInput, VideoInput};
use std::path::{Path, PathBuf};
use tempfile::TempDir;

fn sequence_folder() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/video/seq")
}

fn rate(text: &str) -> FrameRate {
    FrameRate::parse(text).expect("a frame rate")
}

/// The fixture sequence in `folder`, from image `start_number`, playing at `frame_rate`.
fn sequence_in(folder: &Path, start_number: u32, frame_count: u64, frame_rate: &str) -> VideoInput {
    VideoInput::ImageSequence(ImageSequenceInput {
        pattern: folder.join("%06d.jpg"),
        start_number,
        frame_rate: rate(frame_rate),
        frame_count,
    })
}

fn fixture_sequence(frame_rate: &str) -> VideoInput {
    sequence_in(&sequence_folder(), 1, 12, frame_rate)
}

/// A private copy of the fixture images, to remove some from.
fn copy_of_the_images() -> TempDir {
    let folder = tempfile::tempdir().expect("a temporary folder");
    for entry in std::fs::read_dir(sequence_folder()).expect("the fixture folder") {
        let path = entry.expect("an entry").path();
        std::fs::copy(&path, folder.path().join(path.file_name().expect("a name"))).expect("copy");
    }
    folder
}

#[test]
fn the_timeline_is_the_frame_rate_times_the_index_and_nothing_is_scanned_or_cached() {
    for text in ["30", "30000/1001", "0.5"] {
        let input = fixture_sequence(text);
        let cache = tempfile::tempdir().expect("a cache folder");
        let mut progress = Vec::new();
        let index = FrameIndex::load_or_build(
            &input,
            Some(cache.path()),
            &CancelToken::new(),
            &mut |value| progress.push(value),
        )
        .unwrap_or_else(|e| panic!("{text}: {e:?}"));

        let expected: Vec<_> = (0..12).map(|i| rate(text).time_of(i).unwrap()).collect();
        assert_eq!(index.timeline().as_slice(), expected, "{text}");
        assert_eq!(index.keyframes().as_slice(), (0..12).collect::<Vec<_>>());
        assert_eq!(index.scan(), None);
        assert_eq!(index.pts_timing(), None);
        assert!(index.warnings().is_empty());
        assert_eq!((index.info().width, index.info().height), (64, 48));
        assert!(progress.is_empty(), "there is nothing to report");
        assert_eq!(
            std::fs::read_dir(cache.path()).unwrap().count(),
            0,
            "no cache file for a sequence"
        );
    }
    // 30000/1001 is not a whole number of microseconds per frame: frame 1 is at 33 367 us.
    let index = index_of_input(&fixture_sequence("30000/1001"));
    assert_eq!(index.timeline().as_slice()[1].as_us(), 33_367);
}

#[test]
fn frame_i_decodes_to_image_i_with_the_identity_of_the_index_at_any_thread_count() {
    for threads in [1, 4] {
        let input = fixture_sequence("30000/1001");
        let index = index_of_input(&input);
        let mut decoder = open_input(&input, &index, threads);
        let results = read_all(&mut decoder);
        assert_eq!(results.len(), 12);
        for (i, result) in results.into_iter().enumerate() {
            let frame = result.unwrap_or_else(|e| panic!("frame {i}: {e:?}"));
            assert_eq!(frame.id(), index.timeline().frame(i as u64).unwrap());
            assert_eq!(flat_luma(&frame), expected_luma(i as u64), "frame {i}");
            assert!(frame.is_keyframe());
        }
        assert_eq!(decoder.position(), None);
    }
}

#[test]
fn seeking_to_any_frame_lands_on_it_forward_backward_and_from_the_start() {
    let input = fixture_sequence("30");
    let index = index_of_input(&input);
    let mut decoder = open_input(&input, &index, 3);
    for target in [7, 3, 11, 0, 5, 5, 10] {
        decoder
            .seek_to_keyframe(target)
            .expect("every frame is a keyframe");
        assert_eq!(decoder.position(), Some(target));
        for sequence_id in target..(target + 2).min(12) {
            let frame = decoder.next_frame().expect("decodes").expect("a frame");
            assert_eq!(
                frame.id().sequence_id,
                sequence_id,
                "after seeking to {target}"
            );
            assert_eq!(flat_luma(&frame), expected_luma(sequence_id));
        }
    }
    assert!(decoder.seek_to_keyframe(12).is_err(), "past the last frame");
}

#[test]
fn the_start_number_picks_the_first_image() {
    let input = sequence_in(&sequence_folder(), 4, 5, "30");
    let index = index_of_input(&input);
    assert_eq!(index.timeline().len(), 5);
    let mut decoder = open_input(&input, &index, 2);
    for (i, result) in read_all(&mut decoder).into_iter().enumerate() {
        let frame = result.expect("decodes");
        assert_eq!(flat_luma(&frame), expected_luma(i as u64 + 3), "frame {i}");
    }
}

#[test]
fn a_missing_middle_image_fails_its_own_frame_and_no_other() {
    let folder = copy_of_the_images();
    let input = sequence_in(folder.path(), 1, 12, "30");
    let index = index_of_input(&input);
    assert!(index.warnings().is_empty(), "the last image is there");
    std::fs::remove_file(folder.path().join("000006.jpg")).expect("remove image 6");

    for threads in [1, 4] {
        let mut decoder = open_input(&input, &index, threads);
        let results = read_all(&mut decoder);
        assert_eq!(results.len(), 12, "{threads} threads");
        for (i, result) in results.iter().enumerate() {
            match result {
                Ok(frame) => {
                    assert_ne!(i, 5, "frame 5 has no image");
                    assert_eq!(frame.id().sequence_id, i as u64);
                    assert_eq!(flat_luma(frame), expected_luma(i as u64), "frame {i}");
                }
                Err(failure) => {
                    assert_eq!((i, failure.sequence_id), (5, 5));
                    assert!(failure.error.reason.contains("000006.jpg"), "{failure:?}");
                    assert_eq!(
                        failure.error.location,
                        Some(HostPath::new(folder.path().join("%06d.jpg")).into())
                    );
                }
            }
        }

        // Seeking past the gap and back onto it behaves the same.
        decoder.seek_to_keyframe(8).expect("seeks");
        assert_eq!(
            flat_luma(&decoder.next_frame().unwrap().unwrap()),
            expected_luma(8)
        );
        decoder.seek_to_keyframe(5).expect("seeks");
        assert_eq!(decoder.next_frame().unwrap_err().sequence_id, 5);
        assert_eq!(
            flat_luma(&decoder.next_frame().unwrap().unwrap()),
            expected_luma(6)
        );
    }
}

#[test]
fn a_missing_last_image_is_a_warning_and_a_failed_last_frame() {
    let folder = copy_of_the_images();
    std::fs::remove_file(folder.path().join("000012.jpg")).expect("remove image 12");
    let input = sequence_in(folder.path(), 1, 12, "30");
    let index = index_of_input(&input);

    assert_eq!(index.timeline().len(), 12);
    let [warning] = index.warnings() else {
        panic!("one warning, got {:?}", index.warnings());
    };
    assert!(warning.contains("000012.jpg"), "{warning}");

    let mut decoder = open_input(&input, &index, 2);
    let results = read_all(&mut decoder);
    assert_eq!(results.len(), 12);
    assert!(results[..11].iter().all(Result::is_ok));
    assert_eq!(results[11].as_ref().unwrap_err().sequence_id, 11);
}

#[test]
fn a_sequence_that_cannot_open_fails_with_a_reason_and_its_pattern() {
    let folder = tempfile::tempdir().expect("a folder");
    let empty = sequence_in(folder.path(), 1, 12, "30");
    let no_number = VideoInput::ImageSequence(ImageSequenceInput {
        pattern: sequence_folder().join("000001.jpg"),
        start_number: 1,
        frame_rate: rate("30"),
        frame_count: 12,
    });
    let no_images = sequence_in(&sequence_folder(), 1, 0, "30");
    let too_long = sequence_in(&sequence_folder(), 1, 1_000_000_000, "30");
    for input in [empty, no_number, no_images] {
        let Err(Interrupt::Failed(error)) =
            FrameIndex::load_or_build(&input, None, &CancelToken::new(), &mut |_| {})
        else {
            panic!("{input:?} should fail to index");
        };
        assert_explained(&error, input.location());
    }
    let Err(Interrupt::Failed(error)) =
        FrameIndex::load_or_build(&too_long, None, &CancelToken::new(), &mut |_| {})
    else {
        panic!("too long should fail");
    };
    assert_explained(&error, too_long.location());
    assert!(error.reason.contains("seqLength"), "{error:?}");
}
