//! Export speed through the real routes, the real Electron render page and the real encoder.
//!
//! Run with `make bench-export`. Each case exports a generated video with MOT boxes on every frame to an MP4 at the
//! Balanced preset, once on SwiftShader and, when the machine's graphics card can render in the hidden page, once on
//! that. Start-up (Electron, the first frame) is timed apart from the frames after it. Numbers go to stdout, nothing
//! is asserted.
// A bench reports its numbers on stdout.
#![allow(clippy::print_stdout)]

#[path = "../tests/it/common/mod.rs"]
mod common;

use api_types::playback::{
    CatalogRefWire, ClientMessage, ExportFormatWire, ExportLaneWire, ExportQualityWire,
    ExportStateWire,
};
use ax_devil::render_host::RenderHost;
use common::{
    Backend,
    export_kit::{finished, serve_lanes},
};
use export::{LaneStyle, Output, RenderLane, RenderSetup};
use foundation::{CancelToken, FrameSize};
use media::{EncodeFormat, Encoder, Picture, Quality};
use std::{
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const CATALOG: &str = "built-in:standard";
const GRAPHICS: &str = "AX_DEVIL_RENDER_GRAPHICS";

/// (name, width, height, boxes per frame, frames).
const CASES: [(&str, u32, u32, usize, u64); 3] = [
    ("1080p", 1920, 1080, 100, 120),
    ("1080p", 1920, 1080, 1000, 60),
    ("4K", 3840, 2160, 100, 60),
];

/// An MP4 of `frames` pictures at 30 fps: a slow gradient with grain, so the encoders have real work to do.
fn source(dir: &Path, width: u32, height: u32, frames: u64) -> PathBuf {
    let path = dir.join(format!("source-{width}.mp4"));
    let format = EncodeFormat::Mp4 {
        quality: Quality::Small,
    };
    let mut encoder = Encoder::create(&path, format, width, height).expect("an encoder");
    let (w, h) = (width as usize, height as usize);
    for frame in 0..frames {
        let luma: Vec<u8> = (0..w * h)
            .map(|at| {
                let grain = (at as u32 ^ (frame as u32).wrapping_mul(7919))
                    .wrapping_mul(2_654_435_761)
                    >> 28;
                (40 + (at % w / 16) as u32 % 100 + grain) as u8
            })
            .collect();
        let chroma = vec![128u8; w * h / 4];
        let picture = Picture::I420 {
            y: &luma,
            u: &chroma,
            v: &chroma,
            y_stride: w,
            c_stride: w / 2,
        };
        encoder
            .write(picture, frame as i64 * 33_333, 33_333)
            .expect("a frame");
    }
    encoder.finish().expect("a finished video");
    path
}

/// MOT rows: `boxes` pedestrians in a grid, on every one of `frames` frames.
fn boxes(dir: &Path, width: u32, height: u32, boxes: usize, frames: u64) -> PathBuf {
    let path = dir.join(format!("boxes-{boxes}.txt"));
    let columns = (boxes as f64).sqrt().ceil() as u32;
    let (cell_w, cell_h) = (width / columns, height / columns);
    let mut text = String::new();
    for frame in 1..=frames {
        for id in 0..boxes as u32 {
            let (column, row) = (id % columns, id / columns);
            let _ = writeln!(
                text,
                "{frame},{id},{},{},{},{},0.9,1,1",
                column * cell_w + cell_w / 8,
                row * cell_h + cell_h / 8,
                cell_w * 3 / 4,
                cell_h * 3 / 4,
            );
        }
    }
    fs::write(&path, text).expect("the boxes");
    path
}

/// The renderer the page reports with the current environment: the graphics card's name or SwiftShader.
fn renderer() -> String {
    let desktop = ax_devil::desktop::folder().expect("the desktop folder");
    let mut host = RenderHost::start(&desktop, &CancelToken::new()).expect("a render page");
    let catalog = include_str!("../assets/catalogs/minimal.v4.json");
    let style = LaneStyle {
        label: String::new(),
        enabled_classes: Vec::new(),
        class_options: 0,
        hidden: 0,
        theme: None,
        retained_opacity: 1.0,
        filter: None,
    };
    host.setup(RenderSetup {
        output: Output::I420,
        picture: FrameSize {
            width: 64,
            height: 48,
        },
        max_output: FrameSize {
            width: 3840,
            height: 2160,
        },
        lanes: vec![RenderLane {
            source: None,
            catalog: catalog.to_owned(),
            style,
        }],
    })
    .expect("a set up page");
    host.renderer().to_owned()
}

/// The seconds an export of frames 0 to `last` takes, from the request to the finished file.
async fn export_time(
    viewer: &mut common::Viewer,
    lane: &ExportLaneWire,
    last: u64,
    path: &Path,
) -> Duration {
    let started = Instant::now();
    viewer
        .send(&ClientMessage::Export {
            job: last as u32 + 1,
            lanes: vec![lane.clone()],
            first: 0,
            last,
            format: ExportFormatWire::Mp4 {
                quality: ExportQualityWire::Balanced,
            },
            path: path.display().to_string(),
        })
        .await;
    let (_, ended) = finished(viewer, last as u32 + 1).await;
    assert_eq!(ended, ExportStateWire::Done);
    started.elapsed()
}

/// One case on the current environment: (start-up, frames per second after it).
async fn run(
    dir: &Path,
    video: &Path,
    gt: &Path,
    size: (u32, u32),
    frames: u64,
) -> (Duration, f64) {
    let backend = Backend::start().await;
    let added = backend
        .add_with_handlers(
            video,
            &[(gt.to_path_buf(), None, common::mot_options(size))],
        )
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;
    let served = serve_lanes(&mut viewer, false, &[(0, CATALOG)])
        .await
        .remove(0);
    let lane = ExportLaneWire {
        lane: 0,
        catalog: CatalogRefWire {
            id: CATALOG.into(),
            revision: served.catalog,
        },
        style: LaneStyle {
            label: "bench".into(),
            enabled_classes: (0..served.filter.len() as u32)
                .filter(|at| served.filter[*at as usize].default_enabled)
                .collect(),
            class_options: served.filter.len() as u16,
            hidden: 0,
            theme: None,
            retained_opacity: 0.5,
            filter: None,
        },
        policy_revision: 1,
        data_revision: Some(served.data),
    };
    let first = export_time(&mut viewer, &lane, 0, &dir.join("one.mp4")).await;
    let all = export_time(&mut viewer, &lane, frames - 1, &dir.join("all.mp4")).await;
    // The first frame carries the start-up; the others are the rest of the frames' time.
    let rest = all.saturating_sub(first);
    (first, (frames - 1) as f64 / rest.as_secs_f64())
}

fn main() {
    let dir = tempfile::tempdir().expect("temp dir");
    let desktop = Path::new(env!("CARGO_MANIFEST_DIR")).join("desktop");
    // SAFETY: nothing else runs yet: every runtime this bench starts is dropped before the environment is changed.
    unsafe { std::env::set_var("AX_DEVIL_DESKTOP", &desktop) };
    let runtime = || tokio::runtime::Runtime::new().expect("a runtime");
    let set = |graphics: Option<&str>| {
        // SAFETY: as above.
        unsafe {
            match graphics {
                Some(graphics) => std::env::set_var(GRAPHICS, graphics),
                None => std::env::remove_var(GRAPHICS),
            }
        }
    };
    set(None);
    let gpu = renderer();
    set(Some("software"));
    let software = renderer();
    println!("Balanced MP4, one lane, Standard catalog; fps counts the frames after the first");
    println!("software: {software}");
    println!("default:  {gpu}");
    let modes = if gpu == software {
        println!("(the graphics card cannot render in the hidden page here: software only)");
        vec![("software", Some("software"))]
    } else {
        vec![("software", Some("software")), ("gpu", None)]
    };
    for (name, width, height, objects, frames) in CASES {
        let case = tempfile::tempdir_in(dir.path()).expect("case dir");
        let video = source(case.path(), width, height, frames);
        let gt = boxes(case.path(), width, height, objects, frames);
        for (mode, graphics) in &modes {
            set(*graphics);
            let (startup, fps) =
                runtime().block_on(run(case.path(), &video, &gt, (width, height), frames));
            println!(
                "{name} {objects:>4} boxes {mode:>8}: {fps:>6.1} fps, first frame {:.2} s",
                startup.as_secs_f64()
            );
        }
    }
}
