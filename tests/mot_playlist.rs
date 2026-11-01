//! The committed MOT Challenge dataset (`fixtures/datasets/mot/generate.sh`) added as a playlist through the real
//! routes, opened as an entry, and its lanes' overlays read from the socket. The dataset's rows are the expectation:
//! file frame 1 is video frame 0, file frame 6 has no rows in any file, and DPM's `det.txt` has one malformed row.

mod common;

use api_types::playback::{
    ClientMessage, Direction, LaneStatusWire, LaneViewWire, ServerMessage, StickyWire, TargetWire,
};
use common::{Backend, Event, Overlay, Viewer};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
};

const EMPTY_MATCH: u8 = 4;
const EMPTY_FRAME: u64 = 5;
const FRAMES: u64 = 12;
const DPM: u16 = 0;
const GT: u16 = 1;

/// POSTs the dataset to the playlists route and returns the answer's entries.
async fn add_playlist(address: SocketAddr) -> Vec<Value> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/datasets/mot");
    let body = json!({"resolver": "mot_challenge", "settings": {"root": root}}).to_string();
    let answer = tokio::task::spawn_blocking(move || {
        let mut stream = TcpStream::connect(address).unwrap();
        write!(
            stream,
            "POST /api/workspace/playlists HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        response
    })
    .await
    .unwrap();
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");
    let (_, body) = answer.split_once("\r\n\r\n").unwrap();
    let workspace: Value = serde_json::from_str(body).unwrap();
    let item = &workspace["items"][0];
    assert_eq!(item["resolution"]["status"], "Resolved");
    item["resolution"]["content"]["entries"]
        .as_array()
        .unwrap()
        .clone()
}

/// Everything the lanes of an open entry have said so far.
#[derive(Default)]
struct Lanes {
    status: BTreeMap<u16, LaneStatusWire>,
    /// By lane, video frame and revision.
    overlays: BTreeMap<(u16, u64, u32), Overlay>,
}

impl Lanes {
    /// Reads until `done` holds, which bounds the wait through the viewer's own timeout.
    async fn read_until(&mut self, viewer: &mut Viewer, done: impl Fn(&Self) -> bool) {
        while !done(self) {
            match viewer.next().await {
                Event::Message(ServerMessage::LaneStatus { lane, status }) => {
                    self.status.insert(lane, status);
                }
                Event::Overlay(overlay) => {
                    self.overlays.insert(
                        (overlay.lane, overlay.sequence_id, overlay.revision),
                        overlay,
                    );
                }
                Event::Frame(_) => {}
                _ => panic!("expected lane statuses, overlays and frames"),
            }
        }
    }

    fn overlay(&self, lane: u16, frame: u64, revision: u32) -> &Overlay {
        &self.overlays[&(lane, frame, revision)]
    }

    fn has_all(&self, lanes: u16, revision: u32) -> bool {
        (0..lanes).all(|lane| {
            (0..FRAMES).all(|frame| self.overlays.contains_key(&(lane, frame, revision)))
        })
    }
}

fn lane_view(lane: u16, revision: u32, sticky: bool, filter: &[(&str, bool)]) -> ClientMessage {
    ClientMessage::LaneView {
        lane,
        revision,
        view: LaneViewWire {
            catalog: "built-in:standard".into(),
            filter: filter.iter().map(|(id, on)| ((*id).into(), *on)).collect(),
            tolerance_ms: None,
            sticky: StickyWire {
                enabled: sticky,
                timeout_ms: None,
                opacity: 1.0,
            },
            target: TargetWire {
                width: 640,
                height: 480,
            },
            visible: true,
        },
    }
}

#[tokio::test]
async fn the_dataset_opens_as_two_entries_whose_lanes_draw_their_files_rows_frame_by_frame() {
    let backend = Backend::start().await;
    let entries = add_playlist(backend.address).await;
    let summary: Vec<(&str, Vec<&str>)> = entries
        .iter()
        .map(|entry| {
            let lanes = entry["lanes"].as_array().unwrap();
            (
                entry["label"].as_str().unwrap(),
                lanes.iter().map(|lane| lane.as_str().unwrap()).collect(),
            )
        })
        .collect();
    // Folders are listed in path order, so `test/` comes before `train/`.
    assert_eq!(
        summary,
        [
            ("MINI-02", vec!["DET"]),
            ("MINI-01", vec!["DPM", "GT", "FRCNN"])
        ]
    );

    let mut viewer = backend.viewer().await;
    viewer.open(entries[1]["id"].as_str().unwrap(), None).await;
    let video = viewer.opened().await;
    assert_eq!(video.frame_count, FRAMES);
    let labels: Vec<_> = video.lanes.iter().map(|lane| lane.label.as_str()).collect();
    assert_eq!(labels, ["DPM", "GT", "FRCNN"]);

    let mut lanes = Lanes::default();
    viewer
        .want(0, FRAMES as u32 - 1, 0, Direction::Forward)
        .await;
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(3, 0))
        .await;

    // The malformed row is the only one skipped, and only DPM's file has it.
    let LaneStatusWire::Ready {
        filter, warnings, ..
    } = &lanes.status[&DPM]
    else {
        panic!("DPM is ready");
    };
    assert_eq!(filter.len(), 14, "13 MOT classes and the unmapped one");
    let [warning] = &warnings[..] else {
        panic!("one warning, got {warnings:?}");
    };
    assert!(warning.reasons[0].starts_with("line 7:"), "{warning:?}");
    for lane in [GT, 2] {
        let LaneStatusWire::Ready { warnings, .. } = &lanes.status[&lane] else {
            panic!("lane {lane} is ready");
        };
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    // Frame 0: DPM and FRCNN have a tracked and an untracked box, GT a person, a static person and class 99.
    // Frame 3 lost DPM's untracked box to the malformed row.
    let boxes = |lane, frame| lanes.overlay(lane, frame, 0).boxes;
    assert_eq!([boxes(DPM, 0), boxes(GT, 0), boxes(2, 0)], [2, 3, 2]);
    assert_eq!([boxes(DPM, 3), boxes(GT, 3), boxes(2, 3)], [1, 3, 2]);

    // Nothing is drawn on the frame without rows, and a lane that keeps its last object (sticky) still draws none:
    // the file covers every frame, so the frame is empty rather than missing.
    for lane in 0..3 {
        let overlay = lanes.overlay(lane, EMPTY_FRAME, 0);
        assert_eq!((overlay.matched, overlay.boxes), (EMPTY_MATCH, 0));
    }
    for lane in 0..3 {
        viewer.send(&lane_view(lane, 1, true, &[])).await;
    }
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(3, 1))
        .await;
    for lane in 0..3 {
        let overlay = lanes.overlay(lane, EMPTY_FRAME, 1);
        assert_eq!((overlay.matched, overlay.boxes), (EMPTY_MATCH, 0));
        assert!(lanes.overlay(lane, 4, 1).boxes > 0);
    }

    // Turning "Person" off in the GT lane removes the person only; the other lanes are not touched.
    viewer
        .send(&lane_view(GT, 2, true, &[("show_person", false)]))
        .await;
    lanes
        .read_until(&mut viewer, |lanes| {
            (0..FRAMES).all(|frame| lanes.overlays.contains_key(&(GT, frame, 2)))
        })
        .await;
    assert_eq!(lanes.overlay(GT, 0, 2).boxes, 2);
    assert_eq!(
        lanes.overlay(GT, 0, 1).boxes,
        3,
        "the earlier view is unchanged"
    );
    assert!(
        lanes
            .overlays
            .keys()
            .all(|(lane, _, revision)| *lane == GT || *revision < 2)
    );
}

#[tokio::test]
async fn an_entry_without_a_ground_truth_file_has_only_its_detection_lane() {
    let backend = Backend::start().await;
    let entries = add_playlist(backend.address).await;

    let mut viewer = backend.viewer().await;
    viewer.open(entries[0]["id"].as_str().unwrap(), None).await;
    let video = viewer.opened().await;
    assert_eq!(video.lanes.len(), 1);
    assert_eq!(video.lanes[0].label, "DET");

    let mut lanes = Lanes::default();
    viewer
        .want(0, FRAMES as u32 - 1, 0, Direction::Forward)
        .await;
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(1, 0))
        .await;
    let LaneStatusWire::Ready { warnings, .. } = &lanes.status[&0] else {
        panic!("DET is ready");
    };
    assert!(warnings.is_empty());
    assert_eq!(lanes.overlay(0, 0, 0).boxes, 2);
}
