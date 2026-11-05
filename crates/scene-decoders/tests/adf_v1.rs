//! The ADF v1 decoders, through the registry as the host uses it.

use foundation::{CancelToken, Interrupt, MediaTime};
use handler_api::Settings;
use scene::{Event, Scene, SceneTime, Value};
use serde_json::{Value as Json, json};

mod common;

const NOON: i64 = 1_719_835_200_000_000;

/// The Scenes and skipped lines of a file whose lines are `lines`.
fn decode(id: &str, lines: &[String]) -> (Vec<Scene>, Vec<(u64, String)>) {
    let text = lines.join("\n");
    let (gave, result) = common::run_input(
        &*common::decoder(id),
        &mut common::lines(text.as_bytes()),
        &Settings::new(),
        &CancelToken::new(),
    );
    result.expect("a bad line never fails the file");
    (gave.scenes, gave.skipped)
}

/// Why a file fails as a whole.
fn failure(id: &str, lines: &[String]) -> String {
    let text = lines.join("\n");
    let (_, result) = common::run_input(
        &*common::decoder(id),
        &mut common::lines(text.as_bytes()),
        &Settings::new(),
        &CancelToken::new(),
    );
    let Err(Interrupt::Failed(error)) = result else {
        panic!("the file should fail");
    };
    error.0
}

fn time(micros: i64) -> SceneTime {
    SceneTime::Timestamp(MediaTime(NOON + micros))
}

fn detection(id: &str) -> Json {
    json!({
        "object_track_id": id,
        "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
        "class": {"type": "Human", "score": 0.9, "carries_bag": false}
    })
}

fn frame(detections: Vec<Json>, track_events: Vec<Json>) -> Json {
    json!({"timestamp": "2024-07-01T12:00:00+00:00", "detections": detections, "track_events": track_events})
}

#[test]
fn a_frame_gives_its_time_boxes_classes_and_movement_wrapped_or_not() {
    let moving = |value: Json| {
        let mut moving = detection("7");
        moving["moving"] = value;
        frame(vec![moving], vec![])
    };
    let lines = [
        json!({"frame": moving(json!(true))}).to_string(),
        // A timestamp column and a tab come before the record, as in a TSV export.
        format!("2024-07-01T12:00:00Z\t{}", moving(json!(false))),
        moving(json!("false")).to_string(),
    ];
    let (scenes, skipped) = decode("ADF_V1_FRAME", &lines);

    assert!(skipped.is_empty(), "{skipped:?}");
    let states: Vec<_> = scenes
        .iter()
        .map(|scene| scene.entities()[0].motion_state)
        .collect();
    use scene::MotionState::{Moving, Stationary};
    assert_eq!(states, [Some(Moving), Some(Stationary), None]);
    for scene in &scenes {
        assert_eq!(scene.time().start(), time(0));
    }
    let observation = &scenes[0].entities()[0].observations()[0];
    let bounds = observation.geometry.bounds();
    assert_eq!(
        (bounds.left(), bounds.top(), bounds.right(), bounds.bottom()),
        (0.1, 0.2, 0.4, 0.6)
    );
    let class = &observation.classifications[0];
    assert_eq!(class.class_type, "human");
    assert_eq!(class.score.map(scene::Score::value), Some(0.9));
    assert_eq!(class.attribute("carries_bag"), Some(&Value::Bool(false)));
}

#[test]
fn track_events_become_delete_and_rename() {
    let events = vec![
        json!({"type": "TrackEnded", "object_track_id": "old-track"}),
        json!({"type": "Rename", "from_id": "a-1", "to_id": "b-2"}),
    ];
    let (scenes, skipped) = decode("ADF_V1_FRAME", &[frame(vec![], events).to_string()]);

    assert!(skipped.is_empty(), "{skipped:?}");
    let labels: Vec<_> = scenes[0].events().iter().map(Event::label).collect();
    assert_eq!(labels, ["Delete old-track", "Rename a-1 → b-2"]);
}

#[test]
fn a_bad_line_is_skipped_by_its_line_number_and_the_rest_are_read() {
    let mut no_box = detection("1");
    no_box
        .as_object_mut()
        .expect("an object")
        .remove("bounding_box");
    let mut no_id = detection("1");
    no_id
        .as_object_mut()
        .expect("an object")
        .remove("object_track_id");
    let mut unknown_class = detection("1");
    unknown_class["class"]["type"] = json!("Bag");
    let lines = [
        frame(vec![detection("1")], vec![]).to_string(),
        "{not json".to_owned(),
        json!({"detections": []}).to_string(),
        frame(vec![no_box], vec![]).to_string(),
        frame(vec![no_id], vec![]).to_string(),
        frame(vec![], vec![json!({"type": "Rename", "from_id": "a"})]).to_string(),
        frame(vec![], vec![json!({"type": "Merge"})]).to_string(),
        frame(vec![unknown_class], vec![]).to_string(),
        frame(vec![detection("2")], vec![]).to_string(),
    ];
    let (scenes, skipped) = decode("ADF_V1_FRAME", &lines);

    assert_eq!(scenes.len(), 2);
    let positions: Vec<_> = skipped.iter().map(|(position, _)| *position).collect();
    assert_eq!(positions, [2, 3, 4, 5, 6, 7, 8]);
    for ((_, reason), wanted) in skipped.iter().zip([
        "invalid JSON",
        "timestamp",
        "bounding box",
        "object_track_id",
        "Rename to_id",
        "'Merge'",
        "class type \"Bag\"",
    ]) {
        assert!(
            reason.contains(wanted),
            "{reason:?} should mention {wanted}"
        );
    }
}

#[test]
fn a_frame_reads_numbers_written_as_text_and_names_classes_by_its_own_table() {
    let class = |kind: &str, extra: Json| {
        let mut one = detection("1");
        one["class"] = json!({"type": kind, "score": "0.5"});
        one["class"]
            .as_object_mut()
            .expect("an object")
            .extend(extra.as_object().expect("an object").clone());
        frame(vec![one], vec![])
    };
    let mut text_box = detection("1");
    text_box["bounding_box"] =
        json!({"left": "0.1", "top": " 0.2 ", "right": "0.4", "bottom": "0.6"});
    let mut not_finite = detection("1");
    not_finite["bounding_box"]["left"] = json!("NaN");
    // Only Python's table is accepted: `licenseplate` but not `license_plate`.
    let lines = [
        frame(vec![text_box], vec![]).to_string(),
        frame(vec![not_finite], vec![]).to_string(),
        class("Person", json!({})).to_string(),
        class("VehicleOther", json!({})).to_string(),
        class("licensePlate", json!({"plate_number": "ABC"})).to_string(),
        class("license_plate", json!({})).to_string(),
    ];
    let (scenes, skipped) = decode("ADF_V1_FRAME", &lines);

    let classes: Vec<_> = scenes
        .iter()
        .map(|scene| &scene.entities()[0].observations()[0].classifications)
        .collect();
    assert_eq!(skipped.iter().map(|s| s.0).collect::<Vec<_>>(), [2, 6]);
    assert_eq!(
        scenes[0].entities()[0].observations()[0]
            .geometry
            .bounds()
            .top(),
        0.2
    );
    let names: Vec<_> = classes[1..]
        .iter()
        .map(|c| c[0].class_type.as_str())
        .collect();
    assert_eq!(names, ["human", "vehicle_other", "license_plate"]);
    assert_eq!(classes[1][0].score.map(scene::Score::value), Some(0.5));
}

#[test]
fn carries_bag_is_true_as_python_counts_truth() {
    let carries = |value: Json| {
        let mut one = detection("1");
        one["class"]["carries_bag"] = value;
        decode("ADF_V1_FRAME", &[frame(vec![one], vec![]).to_string()]).0[0].entities()[0]
            .observations()[0]
            .classifications[0]
            .attribute("carries_bag")
            .cloned()
    };
    for falsey in [
        json!(false),
        json!(0),
        json!(0.0),
        json!(""),
        json!(null),
        json!([]),
        json!({}),
    ] {
        assert_eq!(
            carries(falsey.clone()),
            Some(Value::Bool(false)),
            "{falsey}"
        );
    }
    for truthy in [
        json!(true),
        json!(1),
        json!("false"),
        json!([0]),
        json!({"a": 0}),
    ] {
        assert_eq!(carries(truthy.clone()), Some(Value::Bool(true)), "{truthy}");
    }
}

fn track(id: &str, times: &[(&str, f64)], class: &str) -> Json {
    let path: Vec<_> = times
        .iter()
        .map(|(at, left)| {
            json!({"timestamp": at, "bounding_box": {"left": left, "top": 0.2, "right": left + 0.3, "bottom": 0.6}})
        })
        .collect();
    json!({
        "id": id,
        "classes": [{"type": class, "score": 0.9}],
        "path": path,
        "start_time": times[0].0,
        "end_time": times[times.len() - 1].0,
        "duration": (times.len() - 1) as f64,
    })
}

#[test]
fn a_consolidated_track_becomes_one_scene_per_observation_time() {
    let mut one = track(
        "t1",
        &[("2024-07-01T12:00:00Z", 0.1), ("2024-07-01T12:00:01Z", 0.2)],
        "Car",
    );
    // The picture's box may be given as `crop_box`; picture and end reason belong to the last observation.
    one["image"] = json!({"timestamp": "2024-07-01T12:00:01Z", "data": "aW1n",
        "crop_box": {"left": 0.2, "top": 0.2, "right": 0.5, "bottom": 0.6}});
    one["end_reason"] = json!("lost");
    let two = track("t2", &[("2024-07-01T12:00:01Z", 0.5)], "Bike");
    let document = json!({"version": "1.0.0", "tracks": [two, null, one]});
    let (scenes, skipped) = decode("ADF_V1_CONSOLIDATED", &[document.to_string()]);

    assert!(skipped.is_empty(), "{skipped:?}");
    let times: Vec<_> = scenes.iter().map(|scene| scene.time().start()).collect();
    assert_eq!(times, [time(0), time(1_000_000)]);
    let ids = |scene: &Scene| -> Vec<String> {
        scene
            .entities()
            .iter()
            .map(|e| e.id().to_string())
            .collect()
    };
    assert_eq!(ids(&scenes[0]), ["t1"]);
    assert_eq!(ids(&scenes[1]), ["t2", "t1"]);
    let late = scenes[1].entity("t1").expect("t1 at the second second");
    assert_eq!(late.observations()[0].geometry.bounds().left(), 0.2);
    assert_eq!(late.observations()[0].classifications[0].class_type, "car");
    assert_eq!(late.images.len(), 1);
    assert_eq!(late.images[0].bounding_box.left(), 0.2);
    assert_eq!(late.end_reason.as_deref(), Some("lost"));
    let early = scenes[0].entity("t1").expect("t1 at the first second");
    assert!(early.images.is_empty() && early.end_reason.is_none());
}

#[test]
fn a_bad_v1_track_fails_the_whole_file() {
    let at = "2024-07-01T12:00:00Z";
    let good = track("t", &[(at, 0.1)], "Car");
    let with = |key: &str, value: Json| {
        let mut changed = good.clone();
        changed[key] = value;
        changed
    };
    let cases = [
        (with("end_time", json!("2024-07-01T12:00:05Z")), "end_time"),
        (with("path", json!([])), "no observations"),
        (with("duration", json!(5.0)), "duration"),
        (with("duration", json!("soon")), "duration"),
        (json!({"object": good}), "id"),
        (json!({"version": "1", "tracks": [3]}), "a track"),
    ];
    for (line, wanted) in cases {
        let reason = failure("ADF_V1_CONSOLIDATED", &[good.to_string(), line.to_string()]);
        assert!(
            reason.starts_with("Line 2: ") && reason.contains(wanted),
            "{reason}"
        );
    }
}

#[test]
fn a_v1_track_takes_any_version_a_zero_id_and_no_duration_and_of_one_id_the_last_track_wins() {
    let at = "2024-07-01T12:00:00Z";
    let mut zero = track("0", &[(at, 0.1)], "Person");
    zero["id"] = json!(0);
    zero["path"][0]["world_position"] = json!({"distance": 4.0, "azimuth": 0, "elevation": 0.5});
    zero.as_object_mut().expect("an object").remove("duration");
    let document = json!({"version": "next", "tracks": [
        track("a", &[(at, 0.1)], "Car"),
        zero,
        track("a", &[("2024-07-01T12:00:01Z", 0.2)], "Bike"),
    ]});
    let (scenes, skipped) = decode("ADF_V1_CONSOLIDATED", &[document.to_string()]);

    assert!(skipped.is_empty(), "{skipped:?}");
    assert_eq!(scenes.len(), 2);
    let first = &scenes[0].entities();
    assert_eq!(first.len(), 1);
    assert_eq!(first[0].id().to_string(), "0");
    let observation = &first[0].observations()[0];
    assert_eq!(observation.classifications[0].class_type, "person");
    assert_eq!(
        observation
            .measurements
            .as_ref()
            .and_then(|m| m.world)
            .map(|w| w.azimuth),
        Some(0.0)
    );
    let second = &scenes[1].entities()[0];
    assert_eq!(second.id().to_string(), "a");
    assert_eq!(
        second.observations()[0].classifications[0].class_type,
        "bike"
    );
}
