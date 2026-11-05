//! Overlay compositing in an export: a faded lane's marks fade as one picture, so where two of them overlap the
//! result is no stronger than where one is, and a polygon's slanted edge is antialiased. Real Electron render page on SwiftShader; skipped without it (see
//! `common::export_kit::can_export`).

mod common;

use api_types::playback::{ExportFormatWire, ExportQualityWire};
use common::export_kit::{assert_near, can_export, decoded, read_png, scene, yuv_at};
use media::VideoInput;

/// Two persons at the time of frame 10 of the variable frame rate video (347 ms), as 32x24 pixel boxes on a 64x48
/// picture at x 8 and x 24, so they overlap from x 24 to 40.
fn two_boxes() -> String {
    let object = |id: u32, left: &str, right: &str| {
        format!(
            r#"<tt:Object ObjectId="{id}"><tt:Appearance><tt:Class><tt:Type Likelihood="0.9">person</tt:Type></tt:Class><tt:Shape><tt:BoundingBox left="{left}" top="0.5" right="{right}" bottom="-0.5" /></tt:Shape></tt:Appearance></tt:Object>"#
        )
    };
    format!(
        r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="1970-01-01T00:00:00.347Z">{}{}</tt:Frame>"#,
        object(1, "-0.75", "0.25"),
        object(2, "-0.25", "0.75")
    )
}
const SOLID_RED: &str = r##"{"schema":4,"name":"Solid","description":"Solid boxes","layers":[{"feature":"outlines","marks":[
    {"mark":"box","stroke":"#ff0000","width":"4px","fill":"#ff0000"}]}]}"##;

#[tokio::test]
async fn overlapping_marks_of_a_kept_sample_fade_as_one() {
    if !can_export() {
        return;
    }
    // The sample is at frame 10; frame 11 keeps it on screen at the lane's retained opacity of one half.
    let mut scene = scene(
        "vfr_h264.mkv",
        (64, 48),
        &[("lane.xml", &two_boxes())],
        &[SOLID_RED.to_owned()],
        true,
    )
    .await;
    let lossless = ExportFormatWire::Mp4 {
        quality: ExportQualityWire::Lossless,
    };
    let path = scene.export(10, 11, lossless, "kept.mp4").await;

    let (_, frames) = decoded(&VideoInput::File(path));
    let kept = &frames[1];
    let one_box = yuv_at(kept, 48, 24);
    let both = yuv_at(kept, 32, 24);
    let picture = yuv_at(kept, 4, 4);
    assert!(
        one_box[2] > picture[2] + 20,
        "a single box shows red through the fade: {one_box:?} over {picture:?}"
    );
    assert_near(both, one_box, 3, "where the two boxes overlap");
}

/// A catalog that fills every polygon with solid red and draws no outline.
const RED_POLYGON: &str = r##"{"schema":4,"name":"Polygon","description":"Solid polygon","layers":[{"feature":"outlines","marks":[
    {"mark":"polygon","stroke":"#ff0000","width":"0px","fill":"#ff0000"}]}]}"##;

#[tokio::test]
async fn a_polygon_edge_across_pixels_is_antialiased() {
    if !can_export() {
        return;
    }
    // A triangle with corners at (8, 6), (56, 6) and (8, 42) of the 64x48 picture; its slanted edge crosses
    // row 24 at x 31.33, inside a pixel.
    let point = |x: &str, y: &str| format!(r#"<tt:Point x="{x}" y="{y}" />"#);
    let xml = format!(
        r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="1970-01-01T00:00:00.347Z"><tt:Object ObjectId="1"><tt:Appearance><tt:Class><tt:Type Likelihood="0.9">person</tt:Type></tt:Class><tt:Shape><tt:Polygon>{}{}{}</tt:Polygon></tt:Shape></tt:Appearance></tt:Object></tt:Frame>"#,
        point("-0.75", "0.75"),
        point("0.75", "0.75"),
        point("-0.75", "-0.75"),
    );
    let mut scene = scene(
        "vfr_h264.mkv",
        (64, 48),
        &[("lane.xml", &xml)],
        &[RED_POLYGON.to_owned()],
        false,
    )
    .await;
    let path = scene
        .export(10, 10, ExportFormatWire::Png, "polygon.png")
        .await;

    let (_, _, rows) = read_png(&path);
    let rgb = |x: usize| -> [i32; 3] {
        let at = (24 * 64 + x) * 4;
        [rows[at], rows[at + 1], rows[at + 2]].map(i32::from)
    };
    assert_eq!(rgb(16), [255, 0, 0], "inside the polygon");
    let picture = rgb(50);
    // Somewhere along the row the edge is neither the polygon nor the picture, in every channel.
    let between = (28..36).any(|x| {
        let pixel = rgb(x);
        (0..3).all(|c| {
            let (low, high) = (
                picture[c].min([255, 0, 0][c]),
                picture[c].max([255, 0, 0][c]),
            );
            pixel[c] > low + 3 && pixel[c] < high - 3
        })
    });
    assert!(
        between,
        "the edge shows a blend: {:?}",
        (28..36).map(rgb).collect::<Vec<_>>()
    );
}
