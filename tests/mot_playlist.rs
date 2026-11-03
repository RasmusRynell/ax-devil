//! The committed MOT Challenge dataset (`fixtures/datasets/mot/generate.sh`) resolved over HTTP and added as a playlist
//! over the Session socket, an entry streamed with a ticket for it, and its lanes' data read from the playback socket: each frame's map
//! cell names a Sample Item with the rows of the file's frame. The dataset's rows are the expectation: file frame 1
//! is video frame 0, file frame 6 has no rows in any file, and DPM's `det.txt` has one malformed row.

mod common;
mod session_client;

use api_types::playback::{
    ClientMessage, Direction, FRAME_MAP_EMPTY, IngestWire, LanePolicyWire, LaneStatusWire,
    ServerMessage, StickyWire,
};
use common::{Backend, Event, Viewer};
use serde_json::{Value, json};
use session_client::Socket;
use std::{collections::BTreeMap, path::Path};

const EMPTY_FRAME: u64 = 5;
const FRAMES: u64 = 12;
const DPM: u16 = 0;
const GT: u16 = 1;

/// A session owner that added the dataset as a playlist, and the playlist as its Doc shows it once resolved.
struct Playlist {
    owner: Socket,
    content: Value,
    seq: u64,
}

impl Playlist {
    async fn add(backend: &Backend) -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/datasets/mot");
        let mut owner = Socket::own(&backend.address.to_string(), None).await;
        let resolved = session_client::post(
            &backend.address.to_string(),
            "/api/resolvers/mot_challenge/resolve",
            &json!({"settings": {"root": root}}),
        )
        .await;
        let add = json!({"type": "content", "command": {"type": "addResolved", "item": resolved["item"], "label": null}});
        owner
            .say(json!({"type": "command", "seq": 1, "cmd": add}))
            .await;
        let doc = owner.settled_doc().await;
        let resolution = &doc["doc"]["workspace"]["items"][0]["resolution"];
        assert_eq!(resolution["status"], "Resolved", "{resolution}");
        Self {
            content: resolution["content"].clone(),
            owner,
            seq: 1,
        }
    }

    fn entries(&self) -> &[Value] {
        self.content["entries"].as_array().unwrap()
    }

    /// A viewer streaming the entry at `index`, through a ticket the owner asked for.
    async fn stream(&mut self, backend: &Backend, index: usize) -> Viewer {
        self.seq += 1;
        let key = json!({
            "content": self.content["id"],
            "generation": self.content["generation"],
            "entry": self.entries()[index]["id"],
        });
        let request = json!({"type": "command", "seq": self.seq, "cmd": {"type": "requestTicket", "key": key}});
        self.owner.say(request).await;
        let seq = self.seq;
        let ticket = self
            .owner
            .until(|message| message["type"] == "ticket" && message["seq"] == seq)
            .await;
        let mut viewer = backend.viewer().await;
        viewer.open(ticket["ticket"].as_str().unwrap(), None).await;
        viewer
    }
}

/// Everything the lanes of an open entry have said so far. A lane is a source of its own: each has its own file.
#[derive(Default)]
struct Lanes {
    status: BTreeMap<u16, LaneStatusWire>,
    /// By lane, policy revision and video frame: the map cell.
    cells: BTreeMap<(u16, u32, u64), u32>,
    /// By source and sample: the entities of the item.
    items: BTreeMap<(u16, u64), u32>,
}

impl Lanes {
    /// Reads until `done` holds, which bounds the wait through the viewer's own timeout.
    async fn read_until(&mut self, viewer: &mut Viewer, done: impl Fn(&Self) -> bool) {
        while !done(self) {
            match viewer.next().await {
                Event::Message(ServerMessage::LaneStatus { lane, status }) => {
                    self.status.insert(lane, status);
                }
                Event::Sample(sample) => {
                    self.items
                        .insert((sample.source, sample.sample), sample.rows);
                }
                Event::Map(map) => {
                    for (k, cell) in map.samples.iter().enumerate() {
                        self.cells
                            .insert((map.lane, map.policy, map.first + k as u64), *cell);
                    }
                }
                Event::Message(ServerMessage::DataWindow { .. } | ServerMessage::Evict { .. })
                | Event::Frame(_) => {}
                _ => panic!("expected lane statuses, data and frames"),
            }
        }
    }

    /// Whether `lanes` lanes have a map cell for every frame under `policy` and every sample those cells name.
    fn has_all(&self, lanes: u16, policy: u32) -> bool {
        (0..lanes).all(|lane| {
            (0..FRAMES).all(|frame| {
                self.cells.get(&(lane, policy, frame)).is_some_and(|cell| {
                    *cell >= FRAME_MAP_EMPTY || self.items.contains_key(&(lane, u64::from(*cell)))
                })
            })
        })
    }

    /// The map cell of a frame.
    fn cell(&self, lane: u16, frame: u64, policy: u32) -> u32 {
        self.cells[&(lane, policy, frame)]
    }

    /// The rows of the file's frame the lane's map names for the video frame, 0 where the map says nothing.
    fn rows(&self, lane: u16, frame: u64, policy: u32) -> u32 {
        match self.cell(lane, frame, policy) {
            cell if cell >= FRAME_MAP_EMPTY => 0,
            cell => self.items[&(lane, u64::from(cell))],
        }
    }
}

fn subscribe(lane: u16, revision: u32, sticky: bool) -> ClientMessage {
    ClientMessage::LanePolicy {
        lane,
        revision,
        policy: LanePolicyWire {
            basis: None,
            tolerance_ms: None,
            sticky: StickyWire {
                enabled: sticky,
                timeout_ms: None,
            },
        },
        subscribed: true,
    }
}

#[tokio::test]
async fn the_dataset_opens_as_two_entries_whose_lanes_map_each_frame_to_its_files_rows() {
    let backend = Backend::start().await;
    let mut playlist = Playlist::add(&backend).await;
    let summary: Vec<(&str, Vec<&str>)> = playlist
        .entries()
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

    let mut viewer = playlist.stream(&backend, 1).await;
    let video = viewer.opened().await;
    assert_eq!(video.frame_count, FRAMES);
    let labels: Vec<_> = video.lanes.iter().map(|lane| lane.label.as_str()).collect();
    assert_eq!(labels, ["DPM", "GT", "FRCNN"]);

    let mut lanes = Lanes::default();
    viewer
        .want(0, FRAMES as u32 - 1, 0, Direction::Forward)
        .await;
    for lane in 0..3 {
        viewer.send(&subscribe(lane, 1, false)).await;
    }
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(3, 1))
        .await;

    // The malformed row is the only one skipped, and only DPM's file has it.
    let LaneStatusWire::Ready {
        filter,
        warnings,
        ingest,
        ..
    } = &lanes.status[&DPM]
    else {
        panic!("DPM is ready");
    };
    assert_eq!(*ingest, IngestWire::Complete);
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

    // Frame 0: DPM and FRCNN have a tracked and an untracked row, GT a person, a static person and class 99. The item
    // keeps every row; hiding a class is the frontend's. Frame 3 lost DPM's untracked row to the malformed row.
    let rows = |lane, frame| lanes.rows(lane, frame, 1);
    assert_eq!([rows(DPM, 0), rows(GT, 0), rows(2, 0)], [2, 3, 2]);
    assert_eq!([rows(DPM, 3), rows(GT, 3), rows(2, 3)], [1, 3, 2]);

    // Nothing is mapped on the frame without rows, and a lane that keeps its last object (sticky) still maps none:
    // the file covers every frame, so the frame is empty rather than missing.
    for lane in 0..3 {
        assert_eq!(lanes.cell(lane, EMPTY_FRAME, 1), FRAME_MAP_EMPTY);
    }
    for lane in 0..3 {
        viewer.send(&subscribe(lane, 2, true)).await;
    }
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(3, 2))
        .await;
    for lane in 0..3 {
        assert_eq!(lanes.cell(lane, EMPTY_FRAME, 2), FRAME_MAP_EMPTY);
        assert!(lanes.rows(lane, 4, 2) > 0);
    }
}

#[tokio::test]
async fn an_entry_without_a_ground_truth_file_has_only_its_detection_lane() {
    let backend = Backend::start().await;
    let mut playlist = Playlist::add(&backend).await;

    let mut viewer = playlist.stream(&backend, 0).await;
    let video = viewer.opened().await;
    assert_eq!(video.lanes.len(), 1);
    assert_eq!(video.lanes[0].label, "DET");

    let mut lanes = Lanes::default();
    viewer
        .want(0, FRAMES as u32 - 1, 0, Direction::Forward)
        .await;
    viewer.send(&subscribe(0, 1, false)).await;
    lanes
        .read_until(&mut viewer, |lanes| lanes.has_all(1, 1))
        .await;
    let LaneStatusWire::Ready { warnings, .. } = &lanes.status[&0] else {
        panic!("DET is ready");
    };
    assert!(warnings.is_empty());
    assert_eq!(lanes.rows(0, 0, 1), 2);
}
