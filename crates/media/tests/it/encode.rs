//! Pictures to files: an MP4 keeps the time of every frame, and a still keeps every pixel.

use crate::common;

use common::{index_of, index_of_input, open, open_input, read_all};
use foundation::FrameRate;
use media::{EncodeFormat, Encoder, ImageSequenceInput, Picture, Quality, VideoInput};

const WIDTH: u32 = 64;
const HEIGHT: u32 = 48;

/// (start, duration) in microseconds of five pictures at a changing rate, one of them 400 ms long.
const TIMES: [(i64, i64); 5] = [
    (0, 33_333),
    (33_333, 66_667),
    (100_000, 33_333),
    (133_333, 400_000),
    (533_333, 16_667),
];

/// A flat picture whose luma and chroma depend on `n`; the planes have padding after each row.
fn flat(n: u8) -> (Vec<u8>, Vec<u8>, Vec<u8>) {
    let luma = 40 + 40 * n;
    let (y_stride, c_stride) = (WIDTH as usize + 8, WIDTH as usize / 2 + 4);
    (
        vec![luma; y_stride * HEIGHT as usize],
        vec![100 + n; c_stride * HEIGHT as usize / 2],
        vec![200 - n; c_stride * HEIGHT as usize / 2],
    )
}

#[test]
fn an_mp4_of_every_quality_keeps_the_times_the_frames_were_given_and_their_pictures() {
    for quality in [
        Quality::Lossless,
        Quality::High,
        Quality::Balanced,
        Quality::Small,
    ] {
        an_mp4_keeps_times_and_pictures(quality);
    }
}

fn an_mp4_keeps_times_and_pictures(quality: Quality) {
    let folder = tempfile::tempdir().expect("a temporary folder");
    let path = folder.path().join("out.mp4");
    let mut encoder = Encoder::create(&path, EncodeFormat::Mp4 { quality }, WIDTH, HEIGHT, None)
        .expect("an encoder");
    for (n, (start, duration)) in TIMES.into_iter().enumerate() {
        let (y, u, v) = flat(n as u8);
        let picture = Picture::I420 {
            y: &y,
            u: &u,
            v: &v,
            y_stride: WIDTH as usize + 8,
            c_stride: WIDTH as usize / 2 + 4,
        };
        encoder.write(picture, start, duration).expect("a frame");
    }
    encoder.finish().expect("a finished file");

    let index = index_of(&path);
    let times: Vec<i64> = index
        .timeline()
        .as_slice()
        .iter()
        .map(|t| t.as_us())
        .collect();
    let expected: Vec<i64> = TIMES.iter().map(|(start, _)| *start).collect();
    assert_eq!(times, expected);
    assert_eq!((index.info().width, index.info().height), (WIDTH, HEIGHT));

    let mut decoder = open(&path, &index, 2);
    let frames = read_all(&mut decoder);
    assert_eq!(frames.len(), TIMES.len());
    for (n, frame) in frames.into_iter().enumerate() {
        let frame = frame.expect("a decoded frame");
        let (luma, blue, red) = (40 + 40 * n as i32, 100 + n as i32, 200 - n as i32);
        for (plane, want) in [(0, luma), (1, blue), (2, red)] {
            for sample in frame.plane(plane) {
                assert!(
                    (i32::from(*sample) - want).abs() <= 3,
                    "frame {n} plane {plane}: {sample} is not close to {want}"
                );
            }
        }
    }
}

#[test]
fn a_png_still_has_the_pixels_it_was_given() {
    let folder = tempfile::tempdir().expect("a temporary folder");
    let path = folder.path().join("0.png");
    let (width, height, stride) = (6usize, 4usize, 6 * 4 + 8);
    let mut rows = vec![0u8; stride * height];
    for y in 0..height {
        for x in 0..width {
            let pixel = [
                (x * 40) as u8,
                (y * 60) as u8,
                (x * 10 + y) as u8,
                255 - (x + y) as u8,
            ];
            rows[y * stride + x * 4..][..4].copy_from_slice(&pixel);
        }
    }
    let mut encoder = Encoder::create(&path, EncodeFormat::Png, width as u32, height as u32, None)
        .expect("an encoder");
    encoder
        .write(
            Picture::Rgba {
                data: &rows,
                stride,
            },
            0,
            1,
        )
        .expect("a picture");
    encoder.finish().expect("a finished file");

    let input = VideoInput::ImageSequence(ImageSequenceInput {
        pattern: folder.path().join("%d.png"),
        start_number: 0,
        frame_rate: FrameRate::parse("1").expect("a rate"),
        frame_count: 1,
    });
    let index = index_of_input(&input);
    let mut decoder = open_input(&input, &index, 1);
    let frames = read_all(&mut decoder);
    assert_eq!(frames.len(), 1);
    let decoded = frames[0].as_ref().expect("a decoded picture");
    for y in 0..height {
        assert_eq!(
            decoded.plane(0)[y * width * 4..][..width * 4],
            rows[y * stride..][..width * 4],
            "row {y}"
        );
    }
}
