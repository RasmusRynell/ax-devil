//! The CVAT decoder, through the registry as the host uses it.

use foundation::{CancelToken, Interrupt};
use handler_api::{DecodeSummary, Settings};
use scene::{Classification, Coverage, Geometry, Scene, Value};

use crate::common;

const SAMPLE: &str = include_str!("../../../../fixtures/decoders/cvat/sample.xml");

fn try_decode(xml: &str) -> Result<(Vec<Scene>, DecodeSummary), String> {
    match common::run(
        &*common::decoder("CVAT"),
        xml.as_bytes(),
        &Settings::new(),
        &CancelToken::new(),
    ) {
        (scenes, Ok(summary)) => Ok((scenes, summary)),
        (_, Err(Interrupt::Failed(error))) => Err(error.0),
        (_, Err(Interrupt::Cancelled)) => panic!("decode was cancelled"),
    }
}

fn decode(xml: &str) -> (Vec<Scene>, DecodeSummary) {
    try_decode(xml).unwrap_or_else(|error| panic!("decode failed: {error}"))
}

fn near(actual: f64, expected: f64) {
    assert!((actual - expected).abs() < 1e-9, "{actual} != {expected}");
}

fn bounds(scene: &Scene, id: &str) -> [f64; 4] {
    let geometry = &scene.entity(id).expect("the entity exists").observations()[0].geometry;
    let Geometry::Box(b) = geometry else {
        panic!("{id} is not a box");
    };
    [b.left(), b.top(), b.right(), b.bottom()]
}

fn class<'a>(scene: &'a Scene, id: &str) -> &'a Classification {
    &scene.entity(id).expect("the entity exists").observations()[0].classifications[0]
}

fn ids(scene: &Scene) -> Vec<&str> {
    scene
        .entities()
        .iter()
        .map(|entity| entity.id().as_str())
        .collect()
}

/// `path` walks maps by key and lists by number.
fn at<'a>(value: &'a Value, path: &[&str]) -> &'a Value {
    path.iter().fold(value, |value, key| match value {
        Value::List(items) => &items[key.parse::<usize>().expect("a list index")],
        _ => {
            let fields = value.as_map().unwrap_or_else(|| panic!("no map at {key}"));
            &fields
                .iter()
                .find(|(name, _)| name == key)
                .unwrap_or_else(|| panic!("no key {key}"))
                .1
        }
    })
}

fn source<'a>(scene: &'a Scene, id: &str) -> &'a Value {
    &scene.entity(id).expect("the entity exists").observations()[0].source_data()[0].1
}

#[test]
fn the_sample_has_two_frames_with_their_shapes_filters_and_metadata() {
    let (scenes, summary) = decode(SAMPLE);

    assert_eq!(scenes.len(), 2);
    assert_eq!(ids(&scenes[0]), ["cvat_track_10"]);
    assert_eq!(ids(&scenes[1]), ["cvat_track_10", "cvat_track_11"]);
    let expected = [100.0 / 1280.0, 200.0 / 720.0, 200.0 / 1280.0, 400.0 / 720.0];
    bounds(&scenes[0], "cvat_track_10")
        .iter()
        .zip(expected)
        .for_each(|(a, e)| near(*a, e));
    let car = class(&scenes[0], "cvat_track_10");
    assert_eq!(car.class_type, "car");
    assert_eq!(car.attribute("color").and_then(Value::as_str), Some("blue"));
    assert_eq!(
        class(&scenes[1], "cvat_track_10").attribute("occluded"),
        Some(&Value::Bool(true))
    );
    let person = class(&scenes[1], "cvat_track_11");
    assert_eq!(person.class_type, "person");
    let Geometry::Polygon(polygon) = &scenes[1]
        .entity("cvat_track_11")
        .expect("polygon")
        .observations()[0]
        .geometry
    else {
        panic!("not a polygon");
    };
    near(polygon.points()[2].x(), 80.0 / 1280.0);
    near(polygon.points()[2].y(), 90.0 / 720.0);

    assert_eq!(
        at(
            source(&scenes[1], "cvat_track_10"),
            &["track", "@attributes", "id"]
        )
        .as_str(),
        Some("10")
    );
    let options = &summary.filter.options;
    assert_eq!(
        options.iter().map(|o| o.id.as_ref()).collect::<Vec<_>>(),
        ["show_car", "show_person"]
    );
    assert_eq!(
        options.iter().map(|o| o.label.as_ref()).collect::<Vec<_>>(),
        ["Car", "Person"]
    );
    let cvat = &summary.metadata[0].1;
    assert_eq!(at(cvat, &["size"]), &Value::Int(2));
    assert_eq!(at(cvat, &["task_name"]).as_str(), Some("Demo Task"));
    assert_eq!(at(cvat, &["labels", "0"]).as_str(), Some("car"));
    assert!(summary.warnings.is_empty());
    assert_eq!(summary.coverage, Coverage::Sparse);
}

#[test]
fn an_outside_shape_leaves_no_entity_in_its_frame() {
    let xml = SAMPLE.replace(
        r#"<box frame="1" outside="0""#,
        r#"<box frame="1" outside="1""#,
    );
    let (scenes, _) = decode(&xml);

    assert_eq!(ids(&scenes[0]), ["cvat_track_10"]);
    assert_eq!(ids(&scenes[1]), ["cvat_track_11"]);
    let unrendered = at(
        &Value::Map(scenes[1].debug().clone()),
        &["cvat", "unrendered_shapes", "0"],
    )
    .clone();
    assert_eq!(
        at(&unrendered, &["shape", "@attributes", "outside"]).as_str(),
        Some("1")
    );
}

#[test]
fn attributes_group_ids_and_unmapped_elements_survive_into_the_scene() {
    let xml = SAMPLE
        .replace(
            r#"<track id="10" label="Car">"#,
            r#"<track id="10" label="Car" group_id="7" source="manual"><attribute name="reviewed">yes</attribute>"#,
        )
        .replace(r#"z_order="0">"#, r#"z_order="0" rotation="15" vendor_field="extra">"#)
        .replace(r#"z_order="1""#, r#"z_order="1" group_id="0""#)
        .replace(
            r#"<attribute name="color">blue</attribute>"#,
            r#"<attribute name="color" origin="review">blue</attribute><extra><value unit="m">12</value></extra>"#,
        );
    let (scenes, _) = decode(&xml);

    let first = class(&scenes[0], "cvat_track_10");
    assert_eq!(first.attribute("group_id"), Some(&Value::Int(7)));
    assert_eq!(
        first.attribute("reviewed").and_then(Value::as_str),
        Some("yes")
    );
    assert_eq!(first.attribute("z_order"), Some(&Value::Int(0)));
    assert_eq!(
        class(&scenes[1], "cvat_track_10").attribute("group_id"),
        Some(&Value::Int(0))
    );
    let raw = source(&scenes[0], "cvat_track_10");
    assert_eq!(
        at(raw, &["track", "@attributes", "source"]).as_str(),
        Some("manual")
    );
    assert_eq!(
        at(raw, &["shape", "@attributes", "vendor_field"]).as_str(),
        Some("extra")
    );
    assert_eq!(
        at(raw, &["shape", "attribute", "#text"]).as_str(),
        Some("blue")
    );
    assert_eq!(
        at(raw, &["shape", "attribute", "@attributes", "origin"]).as_str(),
        Some("review")
    );
    assert_eq!(
        at(raw, &["shape", "extra", "value", "@attributes", "unit"]).as_str(),
        Some("m")
    );
}

const IMAGES: &str = r#"<annotations><version>1.1</version><meta><task><size>3</size></task></meta>
<image id="0" name="frame.jpg" width="200" height="100" subset="validation">
  <box label="Car" group_id="3" xtl="20" ytl="10" xbr="60" ybr="30"/>
  <points label="Person" points="5,6" custom="kept"/>
</image>
<image id="1" name="next.jpg" width="200" height="100"><box label="Car" outside="1" xtl="20" ytl="10" xbr="60" ybr="30"/></image>
<image id="2" name="empty.jpg" width="200" height="100"/></annotations>"#;

#[test]
fn image_documents_size_shapes_by_the_image_and_keep_what_is_not_drawn() {
    let (scenes, summary) = decode(IMAGES);

    assert_eq!(scenes.len(), 3);
    assert_eq!(ids(&scenes[0]), ["cvat_shape_0"]);
    [0.1, 0.1, 0.3, 0.3]
        .iter()
        .zip(bounds(&scenes[0], "cvat_shape_0"))
        .for_each(|(e, a)| near(a, *e));
    assert_eq!(
        class(&scenes[0], "cvat_shape_0").attribute("group_id"),
        Some(&Value::Int(3))
    );
    let raw = source(&scenes[0], "cvat_shape_0");
    assert_eq!(
        at(raw, &["image", "@attributes", "name"]).as_str(),
        Some("frame.jpg")
    );
    let debug = Value::Map(scenes[0].debug().clone());
    assert_eq!(
        at(&debug, &["cvat", "image", "@attributes", "subset"]).as_str(),
        Some("validation")
    );
    assert_eq!(
        at(&debug, &["cvat", "unrendered_shapes", "0", "shape_type"]).as_str(),
        Some("points")
    );
    assert_eq!(
        at(
            &debug,
            &[
                "cvat",
                "unrendered_shapes",
                "0",
                "shape",
                "@attributes",
                "custom"
            ]
        )
        .as_str(),
        Some("kept")
    );
    assert!(scenes[1].entities().is_empty() && scenes[2].entities().is_empty());
    let empty = Value::Map(scenes[2].debug().clone());
    assert_eq!(
        at(&empty, &["cvat", "image", "@attributes", "name"]).as_str(),
        Some("empty.jpg")
    );
    assert_eq!(
        at(
            &summary.metadata[0].1,
            &["all_metadata", "@document", "version"]
        )
        .as_str(),
        Some("1.1")
    );
}

#[test]
fn filters_are_one_per_label_and_a_file_without_labels_gets_unlabeled() {
    let labeled = r#"<a><meta/><image id="0" width="10" height="10">
        <box label="car" xtl="1" ytl="1" xbr="2" ybr="2"/><box label="Person" xtl="1" ytl="1" xbr="2" ybr="2"/>
        <box label="person_on_vehicle" xtl="1" ytl="1" xbr="2" ybr="2"/><box label="car" xtl="3" ytl="3" xbr="4" ybr="4"/>
        </image></a>"#;
    let (_, summary) = decode(labeled);
    let options = &summary.filter.options;
    assert_eq!(
        options.iter().map(|o| o.id.as_ref()).collect::<Vec<_>>(),
        ["show_car", "show_person", "show_person_on_vehicle"]
    );
    assert_eq!(options[2].label, "Person On Vehicle");

    // Labels that differ only in punctuation keep one checkbox each.
    let colliding = r#"<a><meta/><image id="0" width="10" height="10">
        <box label="person/car" xtl="1" ytl="1" xbr="2" ybr="2"/><box label="person car" xtl="1" ytl="1" xbr="2" ybr="2"/>
        </image></a>"#;
    let (_, summary) = decode(colliding);
    let options = &summary.filter.options;
    assert_eq!(
        options
            .iter()
            .map(|o| (o.id.as_ref(), o.rule.types[0].as_ref()))
            .collect::<Vec<_>>(),
        [
            ("show_person_car", "person car"),
            ("show_person_car_2", "person/car")
        ]
    );

    let (_, summary) = decode("<a><meta/></a>");
    let options = &summary.filter.options;
    assert_eq!(
        (options[0].id.as_ref(), options[0].label.as_ref()),
        ("show_unlabeled", "Unlabeled")
    );
    assert!(options[0].rule.matches([]));
}

#[test]
fn a_missing_image_size_counts_as_one_pixel() {
    let (scenes, _) = decode(&SAMPLE.replace("<width>1280</width>", ""));

    let [left, top, right, bottom] = bounds(&scenes[0], "cvat_track_10");
    near(left, 100.0);
    near(top, 200.0 / 720.0);
    near(right, 200.0);
    near(bottom, 400.0 / 720.0);
}

#[test]
fn a_file_without_meta_or_with_broken_xml_fails() {
    let error = try_decode("<annotations><track id=\"1\"/></annotations>").expect_err("no meta");
    assert_eq!(error, "No 'meta' tag found in CVAT XML file");
    let error = try_decode(&SAMPLE.replace("</track>", "</trck>")).expect_err("mismatched tags");
    assert!(error.starts_with("Invalid CVAT XML at line "), "{error}");
}

#[test]
fn a_shape_with_a_bad_frame_is_a_warning_and_a_track_with_a_bad_id_numbers_its_shapes() {
    // The zero-area box is tried and rejected, and still takes number 0.
    let xml = SAMPLE
        .replace(r#"<box frame="1""#, r#"<box frame="x""#)
        .replace(
            r#"<track id="11" label="Person">"#,
            r#"<track id="eleven" label="Person"><box frame="1" xtl="1" ytl="1" xbr="1" ybr="2"/>"#,
        );
    let (scenes, summary) = decode(&xml);

    assert_eq!(scenes.len(), 2);
    assert_eq!(ids(&scenes[0]), ["cvat_track_10"]);
    assert_eq!(ids(&scenes[1]), ["cvat_shape_1"]);
    assert_eq!(
        at(
            source(&scenes[1], "cvat_shape_1"),
            &["track", "@attributes", "id"]
        )
        .as_str(),
        Some("eleven")
    );
    assert_eq!(summary.warnings[0].problem, "Skipped 1 of 4 shapes");
}
