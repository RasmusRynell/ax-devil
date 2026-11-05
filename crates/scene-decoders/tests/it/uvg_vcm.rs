//! The UVG-VCM decoder, through the registry as the host uses it.

use foundation::CancelToken;
use handler_api::{DecodeSummary, Settings};
use scene::{Coverage, Geometry, Scene, SceneTime, Value};
use serde_json::{Value as Json, json};

use crate::common;

fn detection(track_id: i64, class_id: i64) -> Json {
    json!({"track_id": track_id, "class_id": class_id, "x_min": 0.1, "y_min": 0.2, "x_max": 0.4, "y_max": 0.6})
}

fn with(mut detection: Json, key: &str, value: Json) -> Json {
    detection[key] = value;
    detection
}

fn decode(document: &str) -> Result<(Vec<Scene>, DecodeSummary), String> {
    let (scenes, result) = common::run(
        &*common::decoder("UVG_VCM"),
        document.as_bytes(),
        &Settings::new(),
        &CancelToken::new(),
    );
    let summary = result.map_err(|interrupt| format!("{interrupt:?}"))?;
    Ok((scenes, summary))
}

fn decode_frames(frames: Json) -> (Vec<Scene>, DecodeSummary) {
    let mut document = frames;
    document["version"] = json!("1.0");
    decode(&document.to_string()).expect("a valid document")
}

fn class_names(scene: &Scene) -> Vec<&str> {
    scene
        .entities()
        .iter()
        .map(|entity| {
            entity.observations()[0].classifications[0]
                .class_type
                .as_str()
        })
        .collect()
}

fn polygon_points(geometry: &Geometry) -> Vec<[f64; 2]> {
    let Geometry::Polygon(polygon) = geometry else {
        panic!("a polygon");
    };
    polygon.points().iter().map(|p| [p.x(), p.y()]).collect()
}

fn attribute<'a>(scene: &'a Scene, id: &str, name: &str) -> Option<&'a Value> {
    let entity = scene.entity(id)?;
    entity.observations()[0].classifications[0].attribute(name)
}

#[test]
fn frames_keep_their_numbers_and_each_detection_is_kept_as_the_source_wrote_it() {
    let crowd = with(
        with(detection(7, 1), "iscrowd", json!(0)),
        "mask_color",
        json!([155, 255, 255]),
    );
    let outside = with(detection(7, 1), "x_min", json!(-0.000001));
    let document = format!(r#"{{"3": [{outside}], "version": "1.0", "1": [{crowd}], "2": []}}"#);
    let (scenes, summary) = decode(&document).expect("a valid document");

    let times: Vec<_> = scenes.iter().map(|scene| scene.time().start()).collect();
    assert_eq!(
        times,
        [
            SceneTime::Frame(0),
            SceneTime::Frame(1),
            SceneTime::Frame(2)
        ]
    );
    assert!(scenes[1].entities().is_empty());
    assert!(summary.warnings.is_empty());
    assert_eq!(summary.coverage, Coverage::Dense);

    let first = &scenes[0].entities()[0].observations()[0];
    assert_eq!(first.time, SceneTime::Frame(0));
    let Geometry::Box(bounds) = &first.geometry else {
        panic!("a box");
    };
    assert_eq!(
        [bounds.left(), bounds.top(), bounds.right(), bounds.bottom()],
        [0.1, 0.2, 0.4, 0.6]
    );
    let class = &first.classifications[0];
    assert_eq!(
        (class.class_type.as_str(), class.score, class.confidence),
        ("person", None, None)
    );
    assert_eq!(attribute(&scenes[0], "7", "iscrowd"), Some(&Value::Int(0)));
    assert!(
        matches!(attribute(&scenes[0], "7", "mask_color"), Some(Value::List(colors)) if colors.len() == 3)
    );
    let source: Value = serde_json::from_str(&crowd.to_string()).expect("JSON");
    assert_eq!(first.source_data(), &[("uvg_vcm".into(), source)]);

    let Geometry::Box(bounds) = &scenes[2].entities()[0].observations()[0].geometry else {
        panic!("a box");
    };
    assert_eq!(
        bounds.left(),
        -0.000001,
        "coordinates outside 0..1 are not clamped"
    );
}

#[test]
fn category_ids_map_to_names_and_polygons_replace_boxes() {
    let polygon = json!([0.1, 0.2, 0.4, 0.2, 0.3, 0.6]);
    let car = with(
        with(detection(3, 3), "class_name", json!("car")),
        "polygon",
        polygon,
    );
    let (scenes, summary) = decode_frames(
        json!({"1": [car, detection(8, 8), detection(10, 10), detection(33, 33), detection(80, 80)]}),
    );

    assert_eq!(
        class_names(&scenes[0]),
        ["car", "truck", "traffic light", "sports ball", "toothbrush"]
    );
    assert_eq!(
        polygon_points(&scenes[0].entities()[0].observations()[0].geometry),
        [[0.1, 0.2], [0.4, 0.2], [0.3, 0.6]]
    );
    let ids: Vec<_> = summary
        .filter
        .options
        .iter()
        .map(|option| &*option.id)
        .collect();
    assert_eq!(
        ids,
        [
            "show_car",
            "show_sports_ball",
            "show_toothbrush",
            "show_traffic_light",
            "show_truck"
        ]
    );

    let show_car = &summary.filter.options[0];
    assert!(show_car.rule.matches(["car"]) && !show_car.rule.matches(["truck"]));
    assert_eq!(summary.filter.options[3].label, "Traffic Light");
}

#[test]
fn only_the_classes_in_the_file_get_filters_and_an_empty_file_gets_one_for_unclassified() {
    let (_, summary) = decode_frames(json!({"1": [detection(1, 33)]}));
    assert_eq!(summary.filter.options.len(), 1);
    assert_eq!(summary.filter.options[0].id, "show_sports_ball");

    let (scenes, summary) = decode_frames(json!({"1": [], "2": []}));
    assert_eq!(scenes.len(), 2);
    assert!(scenes.iter().all(|scene| scene.entities().is_empty()));
    assert_eq!(summary.filter.options.len(), 1);
    assert_eq!(summary.filter.options[0].id, "show_unclassified");
    assert!(summary.filter.options[0].rule.matches([]));
}

#[test]
fn duplicate_track_ids_become_frame_local_entities_and_a_warning_naming_the_frame() {
    let (scenes, summary) = decode_frames(json!({
        "1": [detection(7, 1), with(detection(7, 1), "x_min", json!(0.2))],
        "2": [detection(7, 1)],
    }));

    let ids: Vec<_> = scenes[0]
        .entities()
        .iter()
        .map(|entity| entity.id().as_str())
        .collect();
    assert_eq!(ids, ["ambiguous:0:7:0", "ambiguous:0:7:1"]);
    assert_eq!(
        attribute(&scenes[0], "ambiguous:0:7:1", "track_id"),
        Some(&Value::Int(7))
    );
    assert_eq!(scenes[1].entities()[0].id(), "7");

    let [warning] = &summary.warnings[..] else {
        panic!("one warning: {:?}", summary.warnings);
    };
    assert_eq!(warning.problem, "Duplicate tracking IDs in 1 of 2 frames");
    assert_eq!(warning.reasons, ["frame 1: [7]"]);
}

#[test]
fn an_invalid_document_fails_the_whole_file_with_frame_and_field_context() {
    let cases = [
        (json!({"version": "0.9", "1": []}), "version '1.0'"),
        (json!({"version": "1.0", "2": []}), "consecutive integers"),
        (
            json!({"version": "1.0", "1": {}}),
            "Frame 1: expected a JSON array",
        ),
        (
            json!({"version": "1.0", "1": [detection(1, 1)], "2": [detection(1, 81)]}),
            "Frame 2 detection 0.class_id",
        ),
        (
            json!({"version": "1.0", "1": [with(detection(1, 1), "class_id", json!(true))]}),
            "class_id",
        ),
        (
            json!({"version": "1.0", "1": [with(detection(1, 1), "x_max", json!(0.0))]}),
            "maxima",
        ),
        (
            json!({"version": "1.0", "1": [with(detection(1, 1), "polygon", json!([0.1, 0.2, 0.3]))]}),
            "coordinate pairs",
        ),
        (
            json!({"version": "1.0", "1": [with(detection(1, 1), "class_name", json!("car"))]}),
            "does not match",
        ),
    ];
    for (document, message) in cases {
        let error = decode(&document.to_string()).expect_err(&document.to_string());
        assert!(error.contains(message), "{document}: {error}");
    }
    assert!(decode("[]").is_err());
}
