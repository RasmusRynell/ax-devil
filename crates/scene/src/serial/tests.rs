use std::{fs, path::PathBuf};

use bytes::Bytes;
use foundation::MediaTime;

use super::*;
use crate::{
    BoundingBox, ClassType, Classification, Confidence, Entity, EntityId, EntityRelation, Event,
    GeographicCoordinates, Geometry, Image, ImageVelocity, Measurements, MotionState, Observation,
    ObservationTime, Operation, Point, Polygon, SceneTime, Score, SphericalCoordinates, TimeSlice,
    Value, WorldVelocity,
};

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures")
}

fn id(text: &str) -> EntityId {
    EntityId::new(text).expect("non-empty")
}

fn bounds(left: f64, top: f64, right: f64, bottom: f64) -> BoundingBox {
    BoundingBox::new_outside(left, top, right, bottom).expect("a box")
}

fn field(key: &'static str, value: Value) -> (crate::Name, Value) {
    (key.into(), value)
}

fn class(name: &'static str, score: Option<f64>, attributes: Fields) -> Classification {
    Classification {
        score: score.map(|s| Score::new(s).expect("finite")),
        attributes,
        ..Classification::new(ClassType::from_static(name))
    }
}

/// A frame-keyed Scene the way the MOT decoder shapes it.
fn mot_frame(frame: i64, boxes: &[(&str, f64)]) -> Scene {
    let entities = boxes
        .iter()
        .map(|&(track, x)| {
            let mut entity = Entity::new(id(track));
            let mut observation = Observation::new(
                SceneTime::Frame(frame),
                Geometry::Box(bounds(x, 0.25, x + 0.125, 0.5)),
            );
            observation.classifications = vec![class(
                "person",
                Some(0.875),
                vec![
                    field("class_id", Value::Int(1)),
                    field("source", Value::Str("det".into())),
                    field("visibility_ratio", Value::Float(1.0)),
                ],
            )];
            entity.push_observation(observation);
            entity
        })
        .collect();
    Scene::new(TimeSlice::at(SceneTime::Frame(frame)))
        .with_entities(entities)
        .expect("one clock")
}

/// A Scene using every value the model has, on the timestamp clock.
fn rich_scene() -> Scene {
    let t = |us| SceneTime::Timestamp(MediaTime(us));
    let nested = Value::Map(vec![
        field("name", Value::Str("red".into())),
        field(
            "rgb",
            Value::List(vec![Value::Int(200), Value::Int(30), Value::Int(30)]),
        ),
        field("score", Value::Float(0.5)),
        field("none", Value::Null),
    ]);
    let mut car = Entity::new(id("car-1"));
    let mut first = Observation::new(t(1_000_000), Geometry::Box(bounds(-0.1, 0.2, 1.2, 0.3)))
        .with_details(
            vec![
                field(
                    "zeta",
                    Value::List(vec![nested.clone(), Value::Bool(false)]),
                ),
                field("alpha", Value::Int(i64::MIN)),
                field("big", Value::Int(i64::MAX)),
            ],
            vec![field("note", Value::Str("é→\u{1F697}".into()))],
        )
        .expect("distinct keys");
    first.classifications = vec![
        Classification {
            confidence: Some(Confidence::new(0.8).expect("finite")),
            ..class(
                "car",
                Some(0.9),
                vec![field("colors", Value::List(vec![nested]))],
            )
        },
        class("?99", None, Fields::new()),
    ];
    first.measurements = Some(Box::new(Measurements {
        image_velocity: Some(ImageVelocity {
            vx: 0.04,
            vy: -0.01,
        }),
        world_velocity: Some(WorldVelocity {
            pitch: 1.0,
            speed: 2.5,
            yaw: -3.0,
        }),
        geographic: Some(GeographicCoordinates {
            latitude: 55.7,
            longitude: 13.2,
            elevation: Some(20.0),
        }),
        world: Some(SphericalCoordinates {
            distance: 12.0,
            azimuth: 0.5,
            elevation: 0.25,
        }),
    }));
    car.push_observation(first);
    let outline = Polygon::new(
        [(0.1, 0.1), (0.5, 0.1), (0.3, 0.6)]
            .map(|(x, y)| Point::new_outside(x, y).expect("finite"))
            .to_vec(),
    )
    .expect("three points");
    // On both clocks, with a frame before the video.
    let both = ObservationTime::new(Some(-3), Some(MediaTime(1_040_000))).expect("a time");
    let mut second = Observation::new(both, Geometry::Polygon(outline));
    second.measurements = Some(Box::new(Measurements {
        geographic: Some(GeographicCoordinates {
            latitude: 1.0,
            longitude: 2.0,
            elevation: None,
        }),
        ..Measurements::default()
    }));
    car.push_observation(second);
    car.images = vec![Image {
        timestamp: MediaTime(1_000_000),
        bounding_box: bounds(0.0, 0.0, 0.5, 0.5),
        data: Bytes::from_static(&[0, 1, 2, 253, 254, 255]),
    }];
    car.end_reason = Some("left the scene".into());
    car.motion_state = Some(MotionState::Moving);
    let mut bare = Entity::new(id("bare"));
    bare.motion_state = Some(MotionState::Stationary);
    let mut unknown = Entity::new(id("unknown"));
    unknown.motion_state = Some(MotionState::Unknown);

    let span = TimeSlice::new(t(1_000_000), t(1_040_000)).expect("forward");
    let events = vec![
        Event::new(span, Operation::Delete { entity: id("bare") }),
        Event::new(
            span,
            Operation::Rename {
                from: id("a"),
                to: id("b"),
            },
        ),
        Event::new(
            TimeSlice::at(t(1_000_000)),
            Operation::Merge {
                sources: vec![id("1"), id("2")],
                target: id("3"),
            },
        ),
        Event::new(
            span,
            Operation::Split {
                source: id("3"),
                targets: vec![id("4"), id("5")],
            },
        ),
    ];
    let relation =
        |kind, from: &str, to: &str| EntityRelation::new(kind, id(from), id(to)).expect("a type");
    Scene::new(span)
        .with_entities(vec![car, bare, unknown])
        .expect("one clock")
        .with_events(events)
        .expect("one clock")
        .with_relations([
            relation("has_part", "car-1", "bare"),
            relation("near", "bare", "car-1"),
        ])
        .with_debug(vec![
            field("decoder", Value::Str("test".into())),
            field("alpha", Value::List(Vec::new())),
        ])
}

fn mot_frames() -> Vec<Scene> {
    vec![
        mot_frame(0, &[("1", 0.125), ("2", 0.5)]),
        mot_frame(1, &[("1", 0.15625), ("untracked:1:1", 0.75)]),
        mot_frame(3, &[("1", 0.1875)]),
    ]
}

fn through_postcard(scene: &Scene) -> Scene {
    let mut bytes = Vec::new();
    encode(scene, &mut bytes).expect("encodes");
    decode(&bytes).expect("decodes")
}

/// A frame-keyed Scene whose observations also carry timestamps, some missing and some against the frame order, so
/// the order the Scene makes (by frame, ties in arrival order) differs from the order of arrival and from any order
/// the timestamps would give.
fn dual_clock_scene() -> Scene {
    let mut entity = Entity::new(id("a"));
    for (x, frame, us) in [
        (0.1, 0, Some(2)),
        (0.2, 1, Some(1)),
        (0.3, 0, None),
        (0.4, 1, Some(0)),
    ] {
        let time = ObservationTime::new(Some(frame), us.map(MediaTime)).expect("a time");
        let geometry = Geometry::Box(bounds(x, 0.25, x + 0.125, 0.5));
        entity.push_observation(Observation::new(time, geometry));
    }
    Scene::new(TimeSlice::at(SceneTime::Frame(0)))
        .with_entities(vec![entity])
        .expect("every observation has a frame")
}

#[test]
fn every_model_value_survives_json_and_postcard() {
    let dual = dual_clock_scene();
    let order: Vec<f64> = dual.entities()[0]
        .observations()
        .iter()
        .map(|o| o.geometry.bounds().left())
        .collect();
    assert_eq!(order, [0.1, 0.3, 0.2, 0.4]);
    let scenes = [
        rich_scene(),
        mot_frame(7, &[("1", 0.5)]),
        mot_frame(8, &[]),
        dual,
    ];

    // A JSON value tree sorts object keys, so it is checked as a document, not as the Scenes.
    let tree = to_json(&scenes);
    assert_eq!(to_json(&from_json(&tree).expect("reads")), tree);
    let text = to_json_string(&scenes).expect("writes");
    assert_eq!(from_json_str(&text).expect("reads"), scenes);
    for scene in &scenes {
        assert_eq!(&through_postcard(scene), scene);
        let line = serde_json::to_vec(&W(scene)).expect("writes");
        assert_eq!(&scene_from_json_slice(&line).expect("reads"), scene);
    }
    assert!(scene_from_json_slice(br#"{"time":{"start":{"frame":1},"end":{"frame":0}}}"#).is_err());
}

#[test]
fn source_values_keep_their_order_in_json_text_and_in_postcard() {
    let scene = rich_scene();
    let order = |scene: &Scene| -> Vec<String> {
        let observation = &scene.entities()[0].observations()[0];
        observation
            .source_data()
            .iter()
            .map(|(key, _)| key.to_string())
            .collect()
    };

    let text = to_json_string(std::slice::from_ref(&scene)).expect("writes");
    let from_text = from_json_str(&text).expect("reads").remove(0);

    assert_eq!(order(&from_text), ["zeta", "alpha", "big"]);
    assert_eq!(order(&through_postcard(&scene)), ["zeta", "alpha", "big"]);
}

#[test]
fn json_leaves_out_what_is_absent_and_postcard_is_smaller() {
    let scene = mot_frame(0, &[("1", 0.125)]);

    let text = to_json_string(std::slice::from_ref(&scene)).expect("writes");

    assert!(
        !text.contains("images") && !text.contains("measurements") && !text.contains("source_data")
    );
    assert!(text.contains(r#""end_reason":null,"motion_state":null"#));
    let mut bytes = Vec::new();
    encode(&scene, &mut bytes).expect("encodes");
    assert!(
        bytes.len() * 3 < text.len(),
        "{} vs {}",
        bytes.len(),
        text.len()
    );
}

fn golden(name: &str, scenes: &[Scene]) {
    let path = fixtures().join("scenes").join(name);
    let expected = fs::read_to_string(&path).expect("golden fixture");

    let pretty = serde_json::to_string_pretty(&write::Document(scenes)).expect("writes");

    assert_eq!(
        format!("{pretty}\n"),
        expected,
        "{name} no longer matches the model's JSON"
    );
    assert_eq!(from_json_str(&expected).expect("golden loads"), scenes);
}

#[test]
fn the_golden_documents_are_stable() {
    golden("mot_frames.json", &mot_frames());
    golden("all_values.json", &[rich_scene()]);
}

#[test]
fn the_reference_apps_scene_dumps_load_and_write_back_the_same() {
    let oracle = fixtures().join("oracle");
    let mut files: Vec<PathBuf> = ["mot/det.scenes.json", "mot/gt.scenes.json"]
        .iter()
        .map(|name| oracle.join(name))
        .collect();
    files.extend(
        fs::read_dir(oracle.join("sheets"))
            .expect("sheets folder")
            .map(|entry| entry.expect("entry").path()),
    );
    assert!(files.len() > 8);

    for path in files {
        let text = fs::read_to_string(&path).expect("oracle file");
        let original: Json = serde_json::from_str(&text).expect("oracle is JSON");

        let scenes = from_json(&original).unwrap_or_else(|e| panic!("{}: {e}", path.display()));

        assert_eq!(to_json(&scenes), original, "{}", path.display());
    }
}

#[test]
fn a_document_that_breaks_a_model_rule_is_refused_with_the_reason() {
    let document = |start: &str, end: &str, entities: &str| {
        format!(
            r#"{{"scene_version":"4.0","scenes":[{{"time":{{"start":{start},"end":{end}}},"entities":[{entities}]}}]}}"#
        )
    };
    let frame = r#"{"frame":0}"#;
    let one = |time: &str, geometry: &str| {
        let observation = format!(r#"{{"time":{time},"geometry":{geometry}}}"#);
        document(
            frame,
            frame,
            &format!(r#"{{"id":"a","observations":[{observation}]}}"#),
        )
    };
    let unit_box = r#"{"box":[0,0,1,1]}"#;
    let cases = [
        (one(frame, unit_box), None),
        (one(r#"{"us":5}"#, unit_box), Some("scene's clock")),
        (one("{}", unit_box), Some("frame number or a timestamp")),
        (one(frame, r#"{"box":[1,0,0,1]}"#), Some("box maxima")),
        (
            one(frame, r#"{"polygon":[[0,0],[1,1]]}"#),
            Some("at least 3"),
        ),
        (
            document(frame, frame, r#"{"id":"","observations":[]}"#),
            Some("entity id"),
        ),
        (document(frame, r#"{"us":0}"#, ""), Some("scene's clock")),
        (document(r#"{"frame":2}"#, frame, ""), Some("ends before")),
    ];

    for (text, wanted) in cases {
        match (from_json_str(&text), wanted) {
            (Ok(_), None) => {}
            (Err(error), Some(reason)) => assert!(error.to_string().contains(reason), "{error}"),
            (other, _) => panic!("unexpected {other:?} for {text}"),
        }
    }
}

#[test]
fn custom_geometry_keeps_independent_bounds_and_optional_outline_on_the_wire() {
    for outline in ["null", "[[-0.1,0],[0.8,0],[0.3,0.6]]"] {
        let text = format!(
            r#"{{"scene_version":"4.2","scenes":[{{"time":{{"start":{{"frame":0}},"end":{{"frame":0}}}},"entities":[{{"id":"custom","observations":[{{"time":{{"frame":0}},"geometry":{{"shape":{{"bounds":[-0.2,0,1.2,1],"outline":{outline}}}}}}}]}}]}}]}}"#
        );
        let scene = from_json_str(&text).unwrap().remove(0);
        let geometry = &scene.entities()[0].observations()[0].geometry;
        assert_eq!(geometry.bounds(), bounds(-0.2, 0.0, 1.2, 1.0));
        assert_eq!(
            geometry.polygon_points().map(<[_]>::len),
            (outline != "null").then_some(3)
        );
        assert_eq!(through_postcard(&scene), scene);
        assert_eq!(
            from_json_str(&to_json_string(std::slice::from_ref(&scene)).unwrap()).unwrap(),
            vec![scene]
        );
    }
}

#[test]
fn a_document_of_another_version_is_refused() {
    let document = |version: &str| format!(r#"{{"scene_version":"{version}","scenes":[]}}"#);

    for accepted in ["4.0", "4.1", "4.2"] {
        assert_eq!(from_json_str(&document(accepted)), Ok(Vec::new()));
    }
    for version in ["5.0", "3.9", "4.3", "four", "4"] {
        assert!(from_json_str(&document(version)).is_err(), "{version}");
    }
    assert!(from_json_str(r#"{"scenes":[]}"#).is_err());
}

#[test]
fn damaged_postcard_records_are_errors_not_scenes() {
    let mut bytes = Vec::new();
    encode(&rich_scene(), &mut bytes).expect("encodes");

    assert!(decode(&bytes[..bytes.len() - 1]).is_err(), "truncated");
    let mut longer = bytes.clone();
    longer.push(0);
    assert!(decode(&longer).is_err(), "trailing bytes");
    assert!(decode(&[]).is_err());
    assert!(decode(&[0xFF; 64]).is_err());
}

#[test]
fn fields_round_trip_on_their_own() {
    let fields = rich_scene().debug().clone();
    let mut bytes = Vec::new();

    encode_fields(&fields, &mut bytes).expect("encodes");

    assert_eq!(decode_fields(&bytes), Ok(fields));
}

#[test]
fn values_nested_past_the_limit_are_refused_without_exhausting_the_stack() {
    // A map of one field whose value is `levels` singleton lists around a null: map(1), key "k", then 5 (List), 1 each.
    let nested = |levels: usize| {
        let mut bytes = vec![1, 1, b'k'];
        for _ in 0..levels {
            bytes.extend([5, 1]);
        }
        bytes.push(0);
        bytes
    };

    assert!(decode_fields(&nested(MAX_VALUE_DEPTH - 1)).is_ok());
    assert!(decode_fields(&nested(MAX_VALUE_DEPTH)).is_err());
    assert!(decode_fields(&nested(1_000_000)).is_err());

    let mut json = Json::Null;
    for _ in 0..=MAX_VALUE_DEPTH {
        json = Json::Array(vec![json]);
    }
    assert!(from_json(&json).is_err());
    // Dropping a document this deep would recurse as well.
    std::mem::forget(json);
}
