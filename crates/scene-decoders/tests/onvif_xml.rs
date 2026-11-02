//! The Axis ONVIF XML decoder, through the registry as the host uses it.

use std::collections::BTreeSet;

use foundation::{CancelToken, FrameSize, MediaTime};
use scene::{Classification, Coverage, Event, Geometry, Scene, SceneTime, Score, Value};
use scene_decoders::{
    DecodeContext, DecodeOptions, RecordSceneDecoder, file_decoder, record_decoder,
};

mod common;

const SAMPLE: &[u8] = include_bytes!("../../../fixtures/decoders/onvif_xml/sample_frame.xml");
const FRAMES: &[u8] = include_bytes!("../../../fixtures/decoders/onvif_xml/frames.xml");
const SAMPLE_TIME: MediaTime = MediaTime(1_741_680_714_008_178);
const NEW_YEAR_2024: i64 = 1_704_067_200_000_000;

fn record_decoder_under_test() -> &'static dyn RecordSceneDecoder {
    record_decoder("ONVIF_XML").expect("the ONVIF record decoder is built in")
}

fn decode(xml: &[u8]) -> Result<Option<Scene>, String> {
    record_decoder_under_test().decode_record(xml)
}

fn decode_scene(xml: &[u8]) -> Scene {
    decode(xml).expect("valid XML").expect("a frame")
}

fn classification<'a>(scene: &'a Scene, id: &str) -> &'a Classification {
    let entity = scene.entity(id);
    &entity.expect("the entity exists").observations()[0].classifications[0]
}

/// The `(r, g, b, score)` of each color in an attribute.
fn colors(classification: &Classification, name: &str) -> BTreeSet<(i64, i64, i64, String)> {
    let Some(Value::List(items)) = classification.attribute(name) else {
        panic!("{name} is a list");
    };
    items
        .iter()
        .map(|item| match item {
            Value::Map(fields) => match (&fields[0].1, &fields[1].1) {
                (Value::List(rgb), Value::Float(score)) => match rgb[..] {
                    [Value::Int(r), Value::Int(g), Value::Int(b)] => (r, g, b, score.to_string()),
                    _ => panic!("rgb is three integers"),
                },
                _ => panic!("a color is rgb and score"),
            },
            _ => panic!("a color is a map"),
        })
        .collect()
}

#[test]
fn sample_frame_gives_time_classes_geometry_events_and_colors() {
    let scene = decode_scene(SAMPLE);

    assert_eq!(scene.time().start(), SceneTime::Timestamp(SAMPLE_TIME));
    let expected = [
        ("101", "bus", 0.75),
        ("102", "car", 0.70),
        ("103", "human", 0.65),
        ("104", "face", 0.60),
        ("105", "license_plate", 0.55),
        ("106", "bike", 0.50),
        ("107", "truck", 0.45),
        ("108", "vehicle", 0.40),
    ];
    assert_eq!(scene.entities().len(), expected.len());
    for (id, class, score) in expected {
        let found = classification(&scene, id);
        assert_eq!(
            (found.class_type.as_str(), found.score.map(Score::value)),
            (class, Some(score))
        );
        assert_eq!(found.confidence, None);
    }

    let Geometry::Box(bounds) = &scene.entities()[0].observations()[0].geometry else {
        panic!("the bounding box wins over the polygon");
    };
    let (left, top, right, bottom) = (bounds.left(), bounds.top(), bounds.right(), bounds.bottom());
    for (actual, wanted) in [(left, 0.2), (top, 0.2), (right, 0.4), (bottom, 0.4)] {
        assert!((actual - wanted).abs() < 1e-9, "{actual} != {wanted}");
    }
    let labels: Vec<_> = scene.events().iter().map(Event::label).collect();
    assert_eq!(labels, ["Rename 38 → 20", "Delete 1"]);

    let expected_colors: BTreeSet<_> = [
        (255, 255, 255),
        (128, 128, 128),
        (0, 0, 0),
        (255, 0, 0),
        (0, 0, 255),
        (0, 128, 0),
        (255, 255, 0),
    ]
    .into_iter()
    .map(|(r, g, b)| (r, g, b, "0.8".to_string()))
    .collect();
    assert!(colors(classification(&scene, "101"), "vehicle_colors").is_superset(&expected_colors));
    let person = classification(&scene, "103");
    assert!(colors(person, "upper_clothing_colors").is_superset(&expected_colors));
    assert!(colors(person, "lower_clothing_colors").is_superset(&expected_colors));
}

#[test]
fn a_missing_likelihood_keeps_the_object_without_inventing_a_score() {
    let xml = String::from_utf8_lossy(SAMPLE)
        .replace(" Likelihood=\"0.75\"", "")
        .replace(" Likelihood=\"0.65\"", "");
    let scene = decode_scene(xml.as_bytes());

    for id in ["101", "103"] {
        let found = classification(&scene, id);
        assert_eq!((found.score, found.confidence), (None, None), "{id}");
    }
    assert_eq!(scene.entities().len(), 8);
}

#[test]
fn unusable_documents_are_errors_and_frameless_streams_are_not() {
    let namespace = r#"xmlns:tt="http://www.onvif.org/ver10/schema""#;
    for (xml, message) in [
        (String::new(), "no element"),
        ("<invalid>xml".to_string(), "left open"),
        ("<root><no_frames/></root>".to_string(), "No <tt:Frame>"),
        (format!("<tt:Frame {namespace}/>"), "UtcTime"),
        (
            format!("<tt:Frame {namespace} UtcTime=\"yesterday\"/>"),
            "UtcTime",
        ),
        (
            format!(
                "<tt:Frame {namespace} UtcTime=\"2024-01-01T00:00:00Z\"><tt:Object ObjectId=\"1\"><tt:Appearance><tt:Shape><tt:BoundingBox left=\"0\"/></tt:Shape></tt:Appearance></tt:Object></tt:Frame>"
            ),
            "BoundingBox top",
        ),
    ] {
        let error = decode(xml.as_bytes()).expect_err(&xml);
        assert!(error.contains(message), "{xml:?}: {error}");
    }

    let event = format!("<tt:MetadataStream {namespace}><tt:Event/></tt:MetadataStream>");
    assert_eq!(decode(event.as_bytes()), Ok(None));
}

#[test]
fn an_object_without_appearance_is_dropped_and_the_frame_keeps_its_time() {
    let xml = r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="2025-01-01T00:00:00.000Z"><tt:Object ObjectId="test"/></tt:Frame>"#;
    let scene = decode_scene(xml.as_bytes());

    assert_eq!(
        scene.time().start(),
        SceneTime::Timestamp(MediaTime(1_735_689_600_000_000))
    );
    assert!(scene.entities().is_empty());
}

#[test]
fn a_file_is_one_document_per_line_and_a_bad_line_is_a_warning() {
    let decoder = file_decoder("ONVIF_XML").expect("the ONVIF file decoder is built in");
    let (scenes, result) = common::run(
        decoder,
        FRAMES,
        &DecodeContext {
            options: &DecodeOptions::new(),
            frame_size: FrameSize {
                width: 1920,
                height: 1080,
            },
        },
        &CancelToken::new(),
    );
    let summary = result.expect("a bad line does not fail the file");

    assert_eq!(summary.coverage, Coverage::Sparse);
    let times: Vec<_> = scenes.iter().map(|scene| scene.time().start()).collect();
    let at = |micros| SceneTime::Timestamp(MediaTime(NEW_YEAR_2024 + micros));
    assert_eq!(times, [at(0), at(1_000_000), at(2_500_000)]);
    assert_eq!(scenes[0].entities()[0].id(), "obj-1");
    assert!(scenes[1].entities().is_empty(), "the nested frame is found");

    let [warning] = &summary.warnings[..] else {
        panic!("one warning: {:?}", summary.warnings);
    };
    assert_eq!(warning.problem, "Skipped 1 of 4 lines");
    assert!(
        warning.reasons[0].starts_with("line 5: "),
        "{:?}",
        warning.reasons
    );
}

#[test]
fn each_class_is_shown_by_exactly_its_own_filter() {
    let policy = record_decoder_under_test().filter_policy();
    let shown = |class: &[&str]| -> Vec<&str> {
        policy
            .options
            .iter()
            .filter(|option| option.rule.matches(class.iter().copied()))
            .map(|option| option.id.as_ref())
            .collect()
    };

    assert_eq!(shown(&["face"]), ["show_faces"]);
    assert_eq!(shown(&["car"]), ["show_cars"]);
    assert_eq!(shown(&[]), ["show_unknown"]);
    assert_eq!(shown(&["unknown"]), ["show_unknown"]);
    assert_eq!(shown(&["zebra"]), ["show_other"]);
    assert_eq!(shown(&["?7"]), ["show_other"]);
    assert!(policy.options.iter().all(|option| option.default_enabled));
}
