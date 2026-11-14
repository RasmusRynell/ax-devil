use super::*;
use crate::fingerprint::Fingerprint;
use crate::index::scan::{Scanned, decode_scan, order, packet_scan};
use std::path::PathBuf;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../fixtures/video")
        .join(name)
}

#[test]
fn the_packet_scan_gives_the_index_the_decode_scan_gives_on_every_fixture() {
    let fixtures = [
        "cfr_h264_gop10_bf2.mp4",
        "long_gop_h264.mp4",
        "open_gop_h264.mkv",
        "vfr_h264.mkv",
        "hevc_gop30.mp4",
        "pts_offset.mp4",
        "yuv444p10_w18.mkv",
        "rgb24_w16.nut",
    ];
    for name in fixtures {
        let path = fixture(name);
        let size = Fingerprint::of_path(&path).expect("fixture").size;
        let scan = |decode: bool| {
            let cancel = CancelToken::new();
            let mut opened = source::open(&path, &cancel).expect("opens");
            let mut progress = |_: f32| {};
            if decode {
                decode_scan(&mut opened, &path, size, &cancel, &mut progress)
                    .map_err(|_| "decode failed")
            } else {
                packet_scan(&mut opened, &path, size, &cancel, &mut progress)
                    .map_err(|_| "scan failed")
                    .and_then(|scanned| scanned.ok_or("needs a decode scan"))
            }
        };
        let (by_packets, by_decoding) = (scan(false), scan(true));
        let (by_packets, by_decoding) = (
            by_packets.unwrap_or_else(|e| panic!("{name}: {e}")),
            by_decoding.unwrap_or_else(|e| panic!("{name}: {e}")),
        );
        assert_eq!(by_packets.pts, by_decoding.pts, "{name}");
        assert_eq!(by_packets.keyframes, by_decoding.keyframes, "{name}");
    }
}

#[test]
fn packets_without_unique_timestamps_cannot_be_indexed_without_decoding() {
    let scanned = |pts: Vec<i64>, keyframes: Vec<u64>, last_duration: i64| {
        Some(Scanned {
            pts,
            keyframes,
            last_duration,
        })
    };
    // The last frame to show is the one with the highest pts, whatever its place in the file.
    let shuffled = vec![
        (Some(30), false, 7),
        (Some(0), true, 10),
        (Some(20), false, 10),
        (Some(10), true, 10),
    ];
    assert_eq!(order(shuffled), scanned(vec![0, 10, 20, 30], vec![0, 1], 7));
    // Decoding always starts at the first frame, flagged or not.
    assert_eq!(
        order(vec![(Some(5), false, 0), (Some(9), true, -1)]),
        scanned(vec![5, 9], vec![0, 1], 0)
    );
    assert_eq!(order(vec![(Some(0), true, 1), (None, false, 1)]), None);
    assert_eq!(
        order(vec![
            (Some(0), true, 1),
            (Some(7), false, 1),
            (Some(7), false, 1)
        ]),
        None
    );
}

#[test]
fn a_last_frame_lasts_the_containers_duration_else_one_declared_frame_period_else_the_previous_gap()
{
    let index_of = |name: &str| {
        FrameIndex::load_or_build(
            &VideoInput::File(fixture(name)),
            None,
            &CancelToken::new(),
            &mut |_| {},
        )
        .expect("indexes")
    };
    // A one-frame video has no gap to fall back on, so it can have no duration at all.
    let mut single = index_of("rgb24_63x47.nut");
    assert_eq!(single.timeline().len(), 1);
    single.last_duration = Some(MediaTime::from_us(2_000_000));
    single.info.frame_rate = FrameRate::new(1, 1);
    assert_eq!(single.duration_of(0), Some(MediaTime::from_us(2_000_000)));
    single.last_duration = None;
    assert_eq!(single.duration_of(0), Some(MediaTime::from_us(1_000_000)));
    single.info.frame_rate = None;
    assert_eq!(single.duration_of(0), None);

    let mut index = index_of("cfr_h264_gop10_bf2.mp4");
    let last = index.timeline().len() - 1;
    let gap = index.timeline().period_after(last - 1).expect("a gap");
    index.last_duration = None;

    index.info.frame_rate = FrameRate::new(1, 1);
    let declared = index.duration_of(last).expect("a duration");
    assert_eq!(declared.as_us(), 1_000_000);
    assert_ne!(declared, gap);

    index.info.frame_rate = None;
    assert_eq!(index.duration_of(last), Some(gap));
}
