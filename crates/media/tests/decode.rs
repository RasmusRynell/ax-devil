//! Frames out of the fixture videos: identity, seeking, failures and delivered pixel layout. Every luma-coded
//! fixture shows frame N as a flat picture of luma (7 * N) mod 256 (`fixtures/video/generate.sh`), so a decoded
//! frame says which frame of the video it is.

mod common;

use common::{assert_explained, expected_luma, fixture, flat_luma, index_of, open, read_all};
use foundation::{CancelToken, Interrupt, PixelFormat, VideoFrame};
use media::{FrameIndex, ScanKind};

/// (file, frame count, keyframe interval).
const LUMA_FIXTURES: [(&str, u64, u64); 6] = [
    ("cfr_h264_gop10_bf2.mp4", 90, 10),
    ("long_gop_h264.mp4", 180, 60),
    ("open_gop_h264.mkv", 90, 30),
    ("hevc_gop30.mp4", 90, 30),
    ("pts_offset.mp4", 60, 10),
    ("vfr_h264.mkv", 60, 20),
];

fn frames_of(name: &str, threads: usize) -> Vec<VideoFrame> {
    let path = fixture(name);
    let index = index_of(&path);
    let mut decoder = open(&path, &index, threads);
    read_all(&mut decoder)
        .into_iter()
        .map(|result| result.unwrap_or_else(|e| panic!("{name}: {e:?}")))
        .collect()
}

#[test]
fn frames_come_in_display_order_with_the_identity_of_the_index() {
    for (name, count, interval) in LUMA_FIXTURES {
        let path = fixture(name);
        let index = index_of(&path);
        assert_eq!(index.scan(), ScanKind::Packet, "{name}");
        assert_eq!(index.timeline().len(), count, "{name}");
        let keyframes: Vec<u64> = (0..count).step_by(interval as usize).collect();
        assert_eq!(index.keyframes().as_slice(), keyframes, "{name}");

        let frames = frames_of(name, 4);
        assert_eq!(frames.len() as u64, count, "{name}");
        for (position, frame) in frames.iter().enumerate() {
            let sequence_id = position as u64;
            assert_eq!(
                frame.id(),
                index
                    .timeline()
                    .frame(sequence_id)
                    .expect("in the timeline"),
                "{name}"
            );
            assert_eq!(
                flat_luma(frame),
                expected_luma(sequence_id),
                "{name} frame {sequence_id} shows another frame"
            );
            assert_eq!(
                frame.is_keyframe(),
                keyframes.contains(&sequence_id),
                "{name} frame {sequence_id}"
            );
        }
    }
}

#[test]
fn constant_rate_times_start_at_zero_even_when_the_container_starts_at_ten_seconds() {
    let step = |n: u64| (n as f64 * 1_000_000.0 / 30.0).round() as i64;
    for name in ["cfr_h264_gop10_bf2.mp4", "pts_offset.mp4"] {
        let index = index_of(&fixture(name));
        let times: Vec<i64> = index
            .timeline()
            .as_slice()
            .iter()
            .map(|t| t.as_us())
            .collect();
        let expected: Vec<i64> = (0..times.len() as u64).map(step).collect();
        assert_eq!(times, expected, "{name}");
    }
}

#[test]
fn variable_frame_rate_times_follow_the_container_timestamps() {
    // generate.sh: frame N is shown at 33 * N + 17 * floor(N / 10) milliseconds.
    let index = index_of(&fixture("vfr_h264.mkv"));
    let times: Vec<i64> = index
        .timeline()
        .as_slice()
        .iter()
        .map(|t| t.as_us())
        .collect();
    let expected: Vec<i64> = (0..60).map(|n| (33 * n + 17 * (n / 10)) * 1000).collect();
    assert_eq!(times, expected);
}

#[test]
fn seeking_to_any_keyframe_gives_the_sequential_pixels_and_the_frames_after_it() {
    for name in [
        "long_gop_h264.mp4",
        "open_gop_h264.mkv",
        "pts_offset.mp4",
        "hevc_gop30.mp4",
    ] {
        let path = fixture(name);
        let index = index_of(&path);
        let reference = frames_of(name, 8);
        let mut decoder = open(&path, &index, 3);
        // Forward through the keyframes, then backward from the last one.
        let keyframes = index.keyframes().as_slice();
        let order = keyframes.iter().chain(keyframes.iter().rev().skip(1));
        for &keyframe in order {
            decoder.seek_to_keyframe(keyframe).expect("a keyframe");
            assert_eq!(decoder.position(), Some(keyframe), "{name}");
            let end = (index.keyframes().group_end(keyframe) + 20).min(reference.len() as u64 - 1);
            for sequence_id in keyframe..=end {
                let frame = decoder
                    .next_frame()
                    .expect("decodes")
                    .expect("not at the end");
                let want = &reference[sequence_id as usize];
                assert_eq!(frame.id(), want.id(), "{name}: after seeking to {keyframe}");
                assert_eq!(
                    frame.data(),
                    want.data(),
                    "{name}: frame {sequence_id} after seeking to {keyframe}"
                );
            }
            assert_eq!(decoder.position(), Some(end + 1), "{name}");
        }
    }
}

#[test]
fn a_seek_to_a_non_keyframe_or_past_the_end_is_rejected_and_reading_goes_on() {
    let path = fixture("cfr_h264_gop10_bf2.mp4");
    let index = index_of(&path);
    let mut decoder = open(&path, &index, 2);
    for _ in 0..5 {
        decoder.next_frame().expect("decodes").expect("a frame");
    }
    for bad in [3, 7, 89, 90, 1_000_000, u64::MAX] {
        let error = decoder.seek_to_keyframe(bad).expect_err("not a keyframe");
        assert_explained(&error, &path);
        assert_eq!(decoder.position(), Some(5), "a rejected seek moves nothing");
    }
    let frame = decoder.next_frame().expect("decodes").expect("a frame");
    assert_eq!(flat_luma(&frame), expected_luma(5));

    decoder.seek_to_keyframe(20).expect("a keyframe");
    let frame = decoder.next_frame().expect("decodes").expect("a frame");
    assert_eq!(flat_luma(&frame), expected_luma(20));
}

#[test]
fn a_missing_file_and_a_file_that_is_not_a_video_fail_at_open_with_a_reason_and_location() {
    let missing = fixture("no_such_file.mp4");
    let not_video = fixture("not_video.mp4");
    for path in [&missing, &not_video] {
        let Err(Interrupt::Failed(error)) =
            FrameIndex::load_or_build(path, None, &CancelToken::new(), &mut |_| {})
        else {
            panic!("{} should fail to index", path.display());
        };
        assert_explained(&error, path);
    }
}

#[test]
fn a_truncated_file_fails_at_its_tail_and_the_frames_before_decode() {
    let path = fixture("truncated.mp4");
    let index = index_of(&path);
    let count = index.timeline().len();
    let mut decoder = open(&path, &index, 2);
    let results = read_all(&mut decoder);

    let first_failure = results
        .iter()
        .position(Result::is_err)
        .expect("the tail fails");
    assert!(
        first_failure as u64 >= count - 3,
        "only the last frames fail, {first_failure} of {count}"
    );
    for (sequence_id, result) in results.iter().enumerate().take(first_failure) {
        let frame = result.as_ref().expect("an earlier frame decodes");
        assert_eq!(flat_luma(frame), expected_luma(sequence_id as u64));
    }
    let failed: Vec<u64> = results[first_failure..]
        .iter()
        .map(|result| {
            result
                .as_ref()
                .expect_err("the tail stays failed")
                .sequence_id
        })
        .collect();
    let expected: Vec<u64> = (first_failure as u64..count).collect();
    assert_eq!(
        failed, expected,
        "each lost frame is reported once, in order"
    );
    let lost = &results[first_failure].as_ref().expect_err("failed").error;
    assert_explained(lost, &path);
    assert!(
        !lost.reason.contains("Frame ") && !lost.reason.contains("frame "),
        "the reason is the cause only; the viewer names the frame: {}",
        lost.reason
    );
    assert_eq!(decoder.position(), None);

    decoder
        .seek_to_keyframe(0)
        .expect("the decoder is still usable");
    assert_eq!(
        flat_luma(&decoder.next_frame().expect("decodes").expect("a frame")),
        expected_luma(0)
    );
}

#[test]
fn frames_are_delivered_tight_in_the_layout_the_info_announces() {
    let frames = frames_of("cfr_h264_gop10_bf2.mp4", 2);
    let info = index_of(&fixture("cfr_h264_gop10_bf2.mp4")).info().clone();
    assert_eq!(
        (info.width, info.height, info.format),
        (64, 48, PixelFormat::I420)
    );
    assert_eq!(info.frame_bytes(), 64 * 48 * 3 / 2);
    for frame in &frames {
        let planes = frame.layout().planes;
        assert_eq!(frame.reserved_bytes(), info.frame_bytes());
        assert_eq!(
            planes.iter().map(|p| p.len as usize).sum::<usize>(),
            frame.data().len()
        );
        assert_eq!(
            [planes[0].stride, planes[1].stride, planes[2].stride],
            [64, 32, 32]
        );
        assert_eq!(
            [
                frame.plane(0).len(),
                frame.plane(1).len(),
                frame.plane(2).len()
            ],
            [64 * 48, 32 * 24, 32 * 24]
        );
    }
}

/// Every pixel of every frame is within `tolerance` of `rgba`.
fn assert_all_pixels(name: &str, size: (u32, u32), rgba: [u8; 4], tolerance: u8) {
    let path = fixture(name);
    let index = index_of(&path);
    assert_eq!(index.info().format, PixelFormat::Rgba, "{name}");
    assert_eq!(index.info().frame_bytes(), (size.0 * size.1 * 4) as usize);
    let frames = frames_of(name, 1);
    assert_eq!(frames.len(), 4);
    for frame in frames {
        assert_eq!(
            (
                frame.layout().width,
                frame.layout().height,
                frame.layout().format
            ),
            (size.0, size.1, PixelFormat::Rgba)
        );
        assert_eq!(
            frame.reserved_bytes(),
            (size.0 * size.1 * 4) as usize,
            "the reservation is the plane bytes"
        );
        assert_eq!(frame.layout().planes[0].stride, size.0 * 4);
        for pixel in frame.data().as_chunks::<4>().0 {
            for (got, want) in pixel.iter().zip(rgba) {
                assert!(
                    got.abs_diff(want) <= tolerance,
                    "{name}: pixel {pixel:?}, expected {rgba:?}"
                );
            }
        }
    }
}

#[test]
fn ten_bit_444_with_an_odd_width_converts_to_rgba_with_the_streams_bt709_matrix() {
    assert_all_pixels("yuv444p10_w18.mkv", (18, 16), [0x40, 0x80, 0xC0, 0xFF], 2);
}

#[test]
fn raw_rgb24_converts_to_rgba_exactly() {
    assert_all_pixels("rgb24_w16.nut", (16, 16), [0x40, 0x80, 0xC0, 0xFF], 0);
}
