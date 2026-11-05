//! The first frame as a JPEG: fitted to the edge without enlarging, for a file and for an image sequence, and the
//! identity that says when it must be made again.

mod common;

use common::{file, fixture, index_of_input, open_input};
use foundation::{CancelToken, FrameRate, Interrupt, VideoFrame};
use media::{
    FirstFrame, ImageSequenceInput, MAX_JPEG_BYTES, VideoInput, first_frame, first_frame_identity,
    whole_jpeg,
};

/// The picture the JPEG holds, found by decoding it as a one-image sequence.
fn decoded(frame: &FirstFrame) -> VideoFrame {
    assert_eq!(&frame.jpeg[..2], [0xFF, 0xD8], "a JPEG starts with SOI");
    let folder = tempfile::tempdir().expect("a temporary folder");
    std::fs::write(folder.path().join("1.jpg"), &frame.jpeg).expect("write the JPEG");
    let input = VideoInput::ImageSequence(ImageSequenceInput {
        pattern: folder.path().join("%d.jpg"),
        start_number: 1,
        frame_rate: FrameRate::parse("1").expect("a rate"),
        frame_count: 1,
    });
    let index = index_of_input(&input);
    open_input(&input, &index, 1)
        .next_frame()
        .expect("the JPEG decodes")
        .expect("a frame")
}

fn decoded_size(frame: &FirstFrame) -> (u32, u32) {
    let picture = decoded(frame);
    (picture.layout().width, picture.layout().height)
}

fn first_of(input: &VideoInput, edge: u32) -> FirstFrame {
    first_frame(input, edge, &CancelToken::new()).unwrap_or_else(|e| panic!("{input:?}: {e:?}"))
}

#[test]
fn the_first_frame_is_a_jpeg_fitted_to_the_edge_and_never_enlarged() {
    let sequence = VideoInput::ImageSequence(ImageSequenceInput {
        pattern: fixture("seq/%06d.jpg"),
        start_number: 1,
        frame_rate: FrameRate::parse("30").expect("a rate"),
        frame_count: 12,
    });
    // (input, edge, size): the 64x48 files, and an odd-sized RGB one.
    let cases = [
        (file(&fixture("cfr_h264_gop10_bf2.mp4")), 32, (32, 24)),
        (file(&fixture("cfr_h264_gop10_bf2.mp4")), 560, (64, 48)),
        (sequence.clone(), 32, (32, 24)),
        (sequence, 560, (64, 48)),
        (file(&fixture("rgb24_63x47.nut")), 40, (40, 30)),
    ];
    for (input, edge, size) in cases {
        let frame = first_of(&input, edge);
        assert_eq!((frame.width, frame.height), size, "{input:?} at {edge}");
        assert_eq!(decoded_size(&frame), size, "{input:?} at {edge}");
    }
}

#[test]
fn a_bt709_video_keeps_its_colour_in_the_jpeg() {
    // Flat sRGB 0x4080C0 tagged BT.709. A JPEG is read as full-range BT.601, where that colour is Y 116, Cb 171,
    // Cr 91 (BT.709 would give Y 119), so the thumbnail must be converted to that matrix.
    let frame = first_of(&file(&fixture("yuv444p10_w18.mkv")), 560);
    let picture = decoded(&frame);
    for (plane, want) in [116u8, 171, 91].into_iter().enumerate() {
        for got in picture.plane(plane) {
            assert!(
                got.abs_diff(want) <= 1,
                "plane {plane}: {got}, expected {want}"
            );
        }
    }
}

#[test]
fn the_identity_holds_until_the_file_is_rewritten() {
    let folder = tempfile::tempdir().expect("a temporary folder");
    let path = folder.path().join("video.mp4");
    std::fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &path).expect("copy");
    let input = file(&path);
    let before = first_frame_identity(&input).expect("an identity");
    assert_eq!(first_frame_identity(&input).expect("again"), before);

    std::fs::copy(fixture("hevc_gop30.mp4"), &path).expect("rewrite");

    assert_ne!(first_frame_identity(&input).expect("changed"), before);
    std::fs::remove_file(&path).expect("remove");
    assert!(first_frame_identity(&input).is_err());
}

#[test]
fn the_identity_of_an_image_sequence_changes_with_its_settings() {
    let sequence = |start_number, rate, frame_count| ImageSequenceInput {
        pattern: fixture("seq/%06d.jpg"),
        start_number,
        frame_rate: FrameRate::parse(rate).expect("a rate"),
        frame_count,
    };
    let identity = |s: ImageSequenceInput| {
        first_frame_identity(&VideoInput::ImageSequence(s)).expect("an identity")
    };
    let base = identity(sequence(1, "30", 12));
    assert_eq!(identity(sequence(1, "30", 12)), base);
    assert_ne!(identity(sequence(2, "30", 12)), base, "start number");
    assert_ne!(identity(sequence(1, "25", 12)), base, "frame rate");
    assert_ne!(identity(sequence(1, "30", 11)), base, "frame count");
}

#[test]
fn a_jpeg_is_whole_only_when_it_decodes_within_the_size_limits() {
    let jpeg = first_of(&file(&fixture("cfr_h264_gop10_bf2.mp4")), 560).jpeg;
    assert!(whole_jpeg(&jpeg, 560));
    assert!(!whole_jpeg(&jpeg[..jpeg.len() / 2], 560), "cut short");
    // Both markers and the headers stay; most of the picture data is garbage.
    let mut damaged = jpeg.clone();
    let scan = jpeg
        .windows(2)
        .position(|w| w == [0xFF, 0xDA])
        .expect("a scan");
    let start = scan + 2 + usize::from(jpeg[scan + 3]) + 16;
    damaged[start..jpeg.len() - 4].fill(0x5A);
    assert!(!whole_jpeg(&damaged, 560), "damaged inside");
    // The 64x48 picture is refused from its header at an edge of 32, and a file over the byte limit unread.
    assert!(whole_jpeg(&jpeg, 64));
    assert!(!whole_jpeg(&jpeg, 32), "larger than the edge");
    let padding = vec![0; usize::try_from(MAX_JPEG_BYTES).expect("fits")];
    let mut huge = jpeg.clone();
    huge.splice(jpeg.len() - 2..jpeg.len() - 2, padding);
    assert!(!whole_jpeg(&huge, 560), "over the byte limit");
}

#[test]
fn a_cancelled_read_is_an_interrupt_and_a_damaged_file_an_error() {
    let cancel = CancelToken::new();
    cancel.cancel();
    let input = file(&fixture("cfr_h264_gop10_bf2.mp4"));
    assert!(matches!(
        first_frame(&input, 560, &cancel),
        Err(Interrupt::Cancelled)
    ));
    assert!(matches!(
        first_frame(&file(&fixture("not_video.mp4")), 560, &CancelToken::new()),
        Err(Interrupt::Failed(_))
    ));
}
