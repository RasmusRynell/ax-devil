//! Prepare time of one lane frame for a MOT20-like frame of 650 boxes: lookup, Scene, class filter, catalog.
//!
//! Run with `cargo bench -p overlays`. The Scene comes from the track's cache after the first call, as it does when
//! playing; the first (cold) call, which reads the Scene from the artifact, is reported apart. How much the catalog
//! step costs depends on the evaluator in `render-catalog`; the number of boxes drawn says whether it ran.
// A bench reports its numbers on stdout.
#![allow(clippy::print_stdout)]

use std::{
    fmt::Write as _,
    hint::black_box,
    time::{Duration, Instant},
};

use foundation::{CancelToken, FrameRate, FrameSize, Timeline};
use handlers::Handlers;
use overlay_sync::MatchPolicy;
use overlays::{LaneView, TargetSize, prepare_lane_frame};
use playlist_resolvers::OverlayRef;
use scene::FilterChoice;
use scene_store::TrackRegistry;

const BOXES: usize = 650;
const FRAMES: u64 = 3;
const SIZE: FrameSize = FrameSize {
    width: 1920,
    height: 1080,
};
const RUNS: usize = 200;

const CATALOGS: [(&str, &str); 3] = [
    (
        "standard",
        include_str!("../../../assets/catalogs/standard.json"),
    ),
    (
        "tracking",
        include_str!("../../../assets/catalogs/tracking.json"),
    ),
    (
        "minimal",
        include_str!("../../../assets/catalogs/minimal.json"),
    ),
];

fn main() {
    let dir = tempfile::tempdir().expect("temp dir");
    let path = dir.path().join("det.txt");
    let mut text = String::new();
    for frame in 1..=FRAMES {
        for id in 0..BOXES {
            let (column, row) = (id % 50, id / 50);
            let _ = writeln!(
                text,
                "{frame},{id},{},{},24,60,0.{},-1,-1,-1",
                20 + column * 37 + frame as usize,
                20 + row * 80,
                50 + id % 50,
            );
        }
    }
    std::fs::write(&path, text).expect("write det.txt");
    let overlay = OverlayRef {
        label: "det".into(),
        path,
        handler: "MOT_FILE".into(),
        options: serde_json::Map::new(),
    };
    let registry = TrackRegistry::new(Handlers::built_in(), None);
    let track = registry
        .open(&overlay, SIZE, &CancelToken::new())
        .expect("the MOT file opens");
    let rate = FrameRate::new(30, 1).expect("rate");
    let times = (0..FRAMES)
        .map(|i| rate.time_of(i).expect("time"))
        .collect();
    let timeline = Timeline::new(times).expect("timeline");
    let frame = timeline.frame(1).expect("frame");
    println!("{BOXES} boxes per frame, {RUNS} runs, median per frame");

    for (name, source) in CATALOGS {
        let catalog = match render_catalog::compile(source) {
            Ok(catalog) => catalog,
            Err(problems) => {
                println!("{name}: does not compile yet ({} problems)", problems.len());
                continue;
            }
        };
        let view = LaneView {
            catalog: catalogs::CatalogId::BuiltIn("standard"),
            filter: FilterChoice::default(),
            policy: MatchPolicy::default(),
            target: TargetSize {
                width: 1280,
                height: 720,
            },
            visible: true,
        };
        let prepare = || prepare_lane_frame(&track, &timeline, &view, &catalog, frame);
        let cold = Instant::now();
        let (_, drawing) = black_box(prepare()).expect("prepared");
        let cold = cold.elapsed();
        let mut times: Vec<Duration> = (0..RUNS)
            .map(|_| {
                let start = Instant::now();
                let _ = black_box(prepare());
                start.elapsed()
            })
            .collect();
        times.sort();
        println!(
            "{name}: cold {cold:?}, median {:?}, worst {:?}; drew {} boxes, {} labels",
            times[RUNS / 2],
            times[RUNS - 1],
            drawing.boxes.len(),
            drawing.labels.len(),
        );
    }
}
