use super::*;
use crate::fingerprint::Fingerprint;
use crate::index::scan::{decode_scan, order, packet_scan};
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
        assert!(by_packets.is_ok(), "{name}: {by_packets:?}");
        assert_eq!(by_packets, by_decoding, "{name}");
    }
}

#[test]
fn packets_without_unique_timestamps_cannot_be_indexed_without_decoding() {
    let shuffled = vec![
        (Some(30), false),
        (Some(0), true),
        (Some(20), false),
        (Some(10), true),
    ];
    assert_eq!(order(shuffled), Some((vec![0, 10, 20, 30], vec![0, 1])));
    // Decoding always starts at the first frame, flagged or not.
    assert_eq!(
        order(vec![(Some(5), false), (Some(9), true)]),
        Some((vec![5, 9], vec![0, 1]))
    );
    assert_eq!(order(vec![(Some(0), true), (None, false)]), None);
    assert_eq!(
        order(vec![(Some(0), true), (Some(7), false), (Some(7), false)]),
        None
    );
}

#[test]
fn two_frames_sharing_a_timestamp_make_the_index_unusable_with_a_reason() {
    let stored = |pts: Vec<i64>| Stored {
        scan: ScanKind::Decode,
        fingerprint: Fingerprint {
            device: 0,
            inode: 0,
            size: 0,
            mtime_ns: 0,
            ctime_ns: 0,
            sample: 0,
        },
        stream: 0,
        time_base: (1, 1000),
        first_pts: pts[0],
        pts,
        keyframes: vec![0],
    };
    assert!(times_of(&stored(vec![5, 15, 25])).is_ok());
    let reason = times_of(&stored(vec![5, 15, 15, 25])).expect_err("a duplicate");
    assert!(reason.contains("frames 1 and 2"), "{reason}");
}
