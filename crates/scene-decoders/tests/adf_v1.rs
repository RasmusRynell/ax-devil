//! The ADF v1 decoders, through the registry as the host uses it.

use foundation::{CancelToken, MediaTime};
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
    let lines = [
        frame(vec![detection("1")], vec![]).to_string(),
        "{not json".to_owned(),
        json!({"detections": []}).to_string(),
        frame(vec![no_box], vec![]).to_string(),
        frame(vec![no_id], vec![]).to_string(),
        frame(vec![], vec![json!({"type": "Rename", "from_id": "a"})]).to_string(),
        frame(vec![], vec![json!({"type": "Merge"})]).to_string(),
        frame(vec![detection("2")], vec![]).to_string(),
    ];
    let (scenes, skipped) = decode("ADF_V1_FRAME", &lines);

    assert_eq!(scenes.len(), 2);
    let positions: Vec<_> = skipped.iter().map(|(position, _)| *position).collect();
    assert_eq!(positions, [2, 3, 4, 5, 6, 7]);
    for ((_, reason), wanted) in skipped.iter().zip([
        "invalid JSON",
        "timestamp",
        "bounding box",
        "object_track_id",
        "Rename to_id",
        "'Merge'",
    ]) {
        assert!(
            reason.contains(wanted),
            "{reason:?} should mention {wanted}"
        );
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
fn a_track_that_contradicts_its_own_times_is_skipped() {
    let mut wrong_end = track("t1", &[("2024-07-01T12:00:00Z", 0.1)], "Car");
    wrong_end["end_time"] = json!("2024-07-01T12:00:05Z");
    let mut empty = track("t2", &[("2024-07-01T12:00:00Z", 0.1)], "Car");
    empty["path"] = json!([]);
    let lines = [
        wrong_end.to_string(),
        empty.to_string(),
        track("t3", &[("2024-07-01T12:00:00Z", 0.1)], "Car").to_string(),
    ];
    let (scenes, skipped) = decode("ADF_V1_CONSOLIDATED", &lines);

    assert_eq!(scenes.len(), 1);
    assert_eq!(skipped.len(), 2);
    assert!(skipped[0].1.contains("end_time"), "{:?}", skipped[0]);
}
