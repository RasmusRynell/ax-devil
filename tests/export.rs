//! Export end to end: the real routes, the real Electron render page on SwiftShader and the real encoder, then the
//! written file decoded and its pixels read. Skipped, with the reason on stderr, when this machine has no Electron or
//! no built render page (`make tests-backend` builds both and then requires them to run); see `common::export_kit::can_export`.

mod common;

use api_types::playback::{
    CatalogRefWire, ClientMessage, ExportFormatWire, ExportLaneWire, ExportQualityWire,
    ExportStateWire,
};
use catalogs::Library;
use common::{
    Backend, Viewer,
    export_kit::{can_export, finished, read_png, serve_lanes},
    fixture,
};
use export::LaneStyle;
use foundation::{CancelToken, VideoFrame};
use media::{Decoder, FrameIndex, VideoInput};
use std::{fs, sync::Arc};

const WIDTH: u32 = 64;
const HEIGHT: u32 = 48;
const RED: [u8; 3] = [255, 0, 0];
const BLUE: [u8; 3] = [0, 0, 255];
/// Frame N of every luma-coded fixture is flat with this luma (fixtures/README.md).
fn luma(frame: u64) -> i32 {
    (frame * 7 % 256) as i32
}

/// A catalog that draws every box as a 4 pixel outline in `color` and nothing else.
fn outline(color: [u8; 3]) -> String {
    let hex = format!("#{:02x}{:02x}{:02x}", color[0], color[1], color[2]);
    format!(
        r#"{{"schema":4,"name":"Outline","description":"Outlines","layers":[{{"feature":"outlines","marks":[
        {{"mark":"box","stroke":"{hex}","width":"4px","fill":"alpha({hex}, 0)"}}]}}]}}"#
    )
}

/// One owner's viewer of a video whose lanes read `overlays` (a file name, which picks the decoder, and its content,
/// per lane) and draw with the catalog of the same position in `colors`; the lanes as an export request names them.
struct Scene {
    // Serves the viewer until the scene ends.
    _backend: Backend,
    viewer: Viewer,
    lanes: Vec<ExportLaneWire>,
    out: tempfile::TempDir,
    _catalogs: tempfile::TempDir,
}

async fn scene(
    video: &str,
    (width, height): (u32, u32),
    overlays: &[(&str, &str)],
    colors: &[[u8; 3]],
    sticky: bool,
) -> Scene {
    let catalogs = tempfile::tempdir().unwrap();
    for (n, color) in colors.iter().enumerate() {
        fs::write(catalogs.path().join(format!("c{n}.json")), outline(*color)).unwrap();
    }
    let backend =
        Backend::start_with_catalogs(Library::with_folder(catalogs.path().to_path_buf())).await;
    // The MOT decoder reads the picture size from the seqinfo.ini beside its file.
    fs::write(
        backend.folder.path().join("seqinfo.ini"),
        format!("[Sequence]\nframeRate=30\nseqLength=100\nimWidth={width}\nimHeight={height}\n"),
    )
    .unwrap();
    let files: Vec<_> = overlays
        .iter()
        .map(|(name, content)| {
            let path = backend.folder.path().join(name);
            fs::write(&path, content).unwrap();
            // Several handlers read .xml; the others are named by their extension.
            (path, name.ends_with(".xml").then_some("ONVIF_XML"))
        })
        .collect();
    let added = backend.add_with_handlers(&fixture(video), &files).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;
    let ids: Vec<_> = (0..overlays.len())
        .map(|n| format!("user:c{n}.json"))
        .collect();
    let wanted: Vec<_> = ids
        .iter()
        .enumerate()
        .map(|(n, id)| (n as u16, id.as_str()))
        .collect();
    let lanes = serve_lanes(&mut viewer, sticky, &wanted)
        .await
        .into_iter()
        .enumerate()
        .map(|(n, served)| ExportLaneWire {
            lane: n as u16,
            catalog: CatalogRefWire {
                id: ids[n].clone(),
                revision: served.catalog,
            },
            style: LaneStyle {
                label: format!("Lane {n}"),
                // As the viewer's menu has them: the file's filters that start on.
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
        })
        .collect();
    Scene {
        _backend: backend,
        viewer,
        lanes,
        out: tempfile::tempdir().unwrap(),
        _catalogs: catalogs,
    }
}

impl Scene {
    /// Exports frames `first` to `last` as `format` to `name` in the output folder and returns the path once the
    /// export is done.
    async fn export(
        &mut self,
        first: u64,
        last: u64,
        format: ExportFormatWire,
        name: &str,
    ) -> std::path::PathBuf {
        let path = self.out.path().join(name);
        self.viewer
            .send(&ClientMessage::Export {
                job: 1,
                lanes: self.lanes.clone(),
                first,
                last,
                format,
                path: path.display().to_string(),
            })
            .await;
        let (_, ended) = finished(&mut self.viewer, 1).await;
        assert_eq!(ended, ExportStateWire::Done);
        path
    }
}

/// The frames of a video file, decoded, and its index.
fn decoded(input: &VideoInput) -> (Arc<FrameIndex>, Vec<VideoFrame>) {
    let index =
        Arc::new(FrameIndex::load_or_build(input, None, &CancelToken::new(), &mut |_| {}).unwrap());
    let mut decoder = Decoder::open(input, index.clone(), 1, CancelToken::new()).unwrap();
    let mut frames = Vec::new();
    while let Some(frame) = decoder.next_frame().unwrap() {
        frames.push(frame);
    }
    (index, frames)
}

/// BT.709 limited range Y, Cb and Cr of an RGB color, as H.264 stores a picture that way.
fn yuv709(rgb: [u8; 3]) -> [i32; 3] {
    let [r, g, b] = rgb.map(|c| f64::from(c) / 255.0);
    let y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    let to_8_bit = |value: f64, offset: f64, span: f64| (offset + span * value).round() as i32;
    [
        to_8_bit(y, 16.0, 219.0),
        to_8_bit((b - y) / 1.8556, 128.0, 224.0),
        to_8_bit((r - y) / 1.5748, 128.0, 224.0),
    ]
}

/// The Y, Cb and Cr samples of pixel (x, y) of a 4:2:0 frame.
fn yuv_at(frame: &VideoFrame, x: usize, y: usize) -> [i32; 3] {
    let planes = frame.layout().planes;
    let at = |plane: usize, x: usize, y: usize| {
        i32::from(frame.plane(plane)[y * planes[plane].stride as usize + x])
    };
    [at(0, x, y), at(1, x / 2, y / 2), at(2, x / 2, y / 2)]
}

/// Asserts the sample triple is within `by` of `want` in every component.
fn assert_near(got: [i32; 3], want: [i32; 3], by: i32, what: &str) {
    assert!(
        got.iter()
            .zip(want)
            .all(|(got, want)| (got - want).abs() <= by),
        "{what}: {got:?} is not within {by} of {want:?}"
    );
}

/// The times of the frames of `index` from `first` to `last`, relative to the first of them.
fn spacing(index: &FrameIndex, first: usize, last: usize) -> Vec<i64> {
    let times: Vec<i64> = index
        .timeline()
        .as_slice()
        .iter()
        .map(|time| time.as_us())
        .collect();
    times[first..=last]
        .iter()
        .map(|time| time - times[first])
        .collect()
}

/// A person in the middle half of the picture (ONVIF's -1 to 1 coordinates, y up) at `time`, which is a time of the
/// video: the box edges are at x 16 and 48, y 12 and 36 of a 64x48 picture.
fn onvif_box(time: &str) -> String {
    format!(
        r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="{time}"><tt:Object ObjectId="1"><tt:Appearance><tt:Class><tt:Type Likelihood="0.9">person</tt:Type></tt:Class><tt:Shape><tt:BoundingBox left="-0.5" top="0.5" right="0.5" bottom="-0.5" /></tt:Shape></tt:Appearance></tt:Object></tt:Frame>"#
    )
}

const LOSSLESS: ExportFormatWire = ExportFormatWire::Mp4 {
    quality: ExportQualityWire::Lossless,
};

#[tokio::test]
async fn one_lane_makes_an_mp4_of_the_picture_size_with_the_source_times_and_the_box_where_its_data_says()
 {
    if !can_export() {
        return;
    }
    // The sample is at the time of frame 10 of the variable frame rate video (347 ms). The lane keeps it on screen,
    // so frames 11 to 14 draw it again from the item the page already holds.
    let xml = onvif_box("1970-01-01T00:00:00.347Z");
    let mut scene = scene(
        "vfr_h264.mkv",
        (WIDTH, HEIGHT),
        &[("lane.xml", &xml)],
        &[RED],
        true,
    )
    .await;
    let path = scene.export(10, 14, LOSSLESS, "clip.mp4").await;

    let (index, frames) = decoded(&VideoInput::File(path));
    assert_eq!((index.info().width, index.info().height), (WIDTH, HEIGHT));
    let (source, _) = decoded(&VideoInput::File(fixture("vfr_h264.mkv")));
    assert_eq!(
        spacing(&index, 0, 4),
        spacing(&source, 10, 14),
        "the frame times"
    );
    assert_eq!(frames.len(), 5);

    let color = frames[0].layout().color;
    assert_eq!(color.matrix, foundation::Matrix::Bt709);
    assert!(!color.full_range);
    // On the edge of the box the outline is the full red, inside the box and far outside it the picture shows.
    for (x, y) in [
        (15, 24),
        (16, 24),
        (32, 11),
        (32, 12),
        (47, 24),
        (48, 24),
        (32, 35),
    ] {
        assert_near(
            yuv_at(&frames[0], x, y),
            yuv709(RED),
            6,
            &format!("edge at {x},{y}"),
        );
    }
    for (x, y) in [(32, 24), (4, 4), (60, 44)] {
        let got = yuv_at(&frames[0], x, y);
        assert_near(got, [luma(10), 128, 128], 4, &format!("picture at {x},{y}"));
    }
    // The kept sample is half as strong over each frame's own picture.
    for (n, frame) in frames.iter().enumerate().skip(1) {
        let picture = luma(10 + n as u64);
        let grey = ((picture - 16) as f64 * 255.0 / 219.0).round() as u8;
        let blended = RED.map(|red| ((u16::from(red) + u16::from(grey)) / 2) as u8);
        assert_near(
            yuv_at(frame, 16, 24),
            yuv709(blended),
            8,
            &format!("kept box on frame {}", 10 + n),
        );
        assert_near(
            yuv_at(frame, 32, 24),
            [picture, 128, 128],
            4,
            "inside the kept box",
        );
    }
}

#[tokio::test]
async fn two_lanes_tile_side_by_side_each_with_its_own_data_catalog_and_name() {
    if !can_export() {
        return;
    }
    // Each lane's box is below the name tag its cell gets; frame 21 of the video is sequence 20.
    let left = "21,1,36,28,20,16,1,1,1\n";
    let right = "21,1,10,28,24,16,1,1,1\n";
    let overlays = [("left.txt", left), ("right.txt", right)];
    let mut scene = scene(
        "cfr_h264_gop10_bf2.mp4",
        (WIDTH, HEIGHT),
        &overlays,
        &[RED, BLUE],
        false,
    )
    .await;
    let path = scene.export(20, 21, LOSSLESS, "grid.mp4").await;

    let (index, frames) = decoded(&VideoInput::File(path));
    assert_eq!(
        (index.info().width, index.info().height),
        (2 * WIDTH, HEIGHT)
    );
    assert_eq!(frames.len(), 2);
    let cell = WIDTH as usize;
    let frame = &frames[0];
    // Lane 0 is the left cell and draws red at its box; lane 1 the right cell, blue at its own box.
    for (x, y) in [(36, 36), (56, 36), (46, 28), (46, 44)] {
        assert_near(
            yuv_at(frame, x, y),
            yuv709(RED),
            6,
            &format!("red edge at {x},{y}"),
        );
    }
    for (x, y) in [(10, 36), (34, 36), (22, 28), (22, 44)] {
        let at = cell + x;
        assert_near(
            yuv_at(frame, at, y),
            yuv709(BLUE),
            6,
            &format!("blue edge at {at},{y}"),
        );
    }
    // Each lane's catalog and data stay in its cell: the other's box position is plain picture.
    for (x, y) in [(36, 36), (56, 36)] {
        assert_near(
            yuv_at(frame, cell + x, y),
            [luma(20), 128, 128],
            4,
            "right cell, left box",
        );
    }
    for (x, y) in [(10, 36), (22, 28)] {
        assert_near(
            yuv_at(frame, x, y),
            [luma(20), 128, 128],
            4,
            "left cell, right box",
        );
    }
    // Both cells carry a name tag, a dark box over the picture in the cell's corner.
    for corner in [0, cell] {
        let tag = yuv_at(frame, corner + 8, 8);
        assert!(
            tag[0] < luma(20) - 20,
            "the tag at the top left of the cell is dark: {tag:?}"
        );
    }
}

#[tokio::test]
async fn a_still_is_a_png_of_the_picture_size_with_the_box_and_the_picture_exact() {
    if !can_export() {
        return;
    }
    let overlays = [("lane.txt", "21,1,28,22,28,20,1,1,1\n")];
    let mut scene = scene(
        "cfr_h264_gop10_bf2.mp4",
        (WIDTH, HEIGHT),
        &overlays,
        &[RED],
        false,
    )
    .await;
    let path = scene
        .export(20, 20, ExportFormatWire::Png, "still.png")
        .await;

    let (width, height, rows) = read_png(&path);
    assert_eq!((width, height), (WIDTH, HEIGHT));
    let rgba = |x: usize, y: usize| {
        let at = (y * WIDTH as usize + x) * 4;
        <[u8; 4]>::try_from(&rows[at..at + 4]).unwrap()
    };
    // The outline is exactly the catalog's red; inside the box and outside it, the frame's grey, which is the
    // limited-range luma 140 of frame 20 as full-range RGB.
    for (x, y) in [
        (27, 32),
        (28, 32),
        (42, 22),
        (42, 23),
        (55, 32),
        (56, 32),
        (42, 41),
    ] {
        assert_eq!(rgba(x, y), [255, 0, 0, 255], "edge at {x},{y}");
    }
    let grey = ((luma(20) - 16) as f64 * 255.0 / 219.0).round() as i32;
    for (x, y) in [(42, 32), (4, 4), (60, 44)] {
        let pixel = rgba(x, y);
        assert!(
            pixel[..3].iter().all(|c| (i32::from(*c) - grey).abs() <= 2) && pixel[3] == 255,
            "picture at {x},{y}: {pixel:?} is not grey {grey}"
        );
    }
}

#[tokio::test]
async fn a_still_of_an_odd_sized_picture_keeps_its_exact_size() {
    if !can_export() {
        return;
    }
    let overlays = [("lane.txt", "1,1,28,22,28,20,1,1,1\n")];
    let mut scene = scene("rgb24_63x47.nut", (63, 47), &overlays, &[RED], false).await;
    let path = scene.export(0, 0, ExportFormatWire::Png, "odd.png").await;

    let (width, height, rows) = read_png(&path);
    assert_eq!((width, height), (63, 47));
    let rgba = |x: usize, y: usize| {
        let at = (y * 63 + x) * 4;
        <[u8; 4]>::try_from(&rows[at..at + 4]).unwrap()
    };
    // The last column and row exist and show the picture's colour 0x4080C0; the box's edge is where its data says.
    for (x, y) in [(62, 46), (62, 0), (0, 46)] {
        let pixel = rgba(x, y);
        let want = [0x40, 0x80, 0xC0];
        assert!(
            pixel[..3]
                .iter()
                .zip(want)
                .all(|(c, w)| (i32::from(*c) - w).abs() <= 2)
                && pixel[3] == 255,
            "picture at {x},{y}: {pixel:?}"
        );
    }
    assert_eq!(rgba(28, 32), [255, 0, 0, 255]);
}
