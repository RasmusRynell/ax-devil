//! Encode time and size of Sample Items for Scenes of 1, 100, 1000 and 5000 entities.
//!
//! Run with `cargo bench -p overlays`. It times `Track::item`'s work, `encode_sample_item`, on a Scene read from a
//! real MOT track (the first, cold call also reads the artifact and is reported apart), so the numbers are what a
//! source thread spends per sample and what the wire carries per object.
// A bench reports its numbers on stdout.
#![allow(clippy::print_stdout)]

use std::{
    fmt::Write as _,
    hint::black_box,
    time::{Duration, Instant},
};

use foundation::CancelToken;
use handlers::Handlers;
use playlist_resolvers::OverlayRef;
use scene::serial::columns::encode_sample_item;
use scene_store::TrackRegistry;

const SIZES: [usize; 4] = [1, 100, 1000, 5000];
const RUNS: usize = 100;

fn main() {
    let dir = tempfile::tempdir().expect("temp dir");
    std::fs::write(
        dir.path().join("seqinfo.ini"),
        "[Sequence]\nframeRate=30\nseqLength=1\nimWidth=1920\nimHeight=1080\n",
    )
    .expect("write seqinfo.ini");
    let registry = TrackRegistry::new(Handlers::built_in(), None);
    println!("{RUNS} runs, median per item");
    for objects in SIZES {
        let path = dir.path().join(format!("det-{objects}.txt"));
        let mut text = String::new();
        for id in 0..objects {
            let (column, row) = (id % 50, id / 50);
            let _ = writeln!(
                text,
                "1,{id},{},{},24,60,0.{},-1,-1,-1",
                20 + column * 37,
                20 + row * 80,
                50 + id % 50,
            );
        }
        std::fs::write(&path, text).expect("write det.txt");
        let overlay = OverlayRef {
            label: "det".into(),
            path,
            handler: "MOT_FILE".into(),
            options: serde_json::Map::new(),
        };
        let track = registry
            .open(&overlay, &CancelToken::new())
            .expect("the MOT file opens");
        let cold = Instant::now();
        let scene = black_box(track.scene(0)).expect("the Scene reads");
        let cold = cold.elapsed();
        let encode = || encode_sample_item(&scene, track.filter());
        let bytes = black_box(encode()).expect("encodes").len();
        let mut times: Vec<Duration> = (0..RUNS)
            .map(|_| {
                let start = Instant::now();
                let _ = black_box(encode());
                start.elapsed()
            })
            .collect();
        times.sort();
        let median = times[RUNS / 2];
        println!(
            "{objects} objects: read {cold:?}, encode {median:?} ({:.0} ns/object), {bytes} bytes ({:.1} bytes/object)",
            median.as_nanos() as f64 / objects as f64,
            bytes as f64 / objects as f64,
        );
    }
}
