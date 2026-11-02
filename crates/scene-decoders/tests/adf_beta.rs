//! The ADF beta decoders, through the registry as the host uses it.

use foundation::{CancelToken, MediaTime};
use handler_api::Settings;
use scene::{Event, Scene, SceneTime, Value};
use serde_json::{Value as Json, json};

mod common;

const NOON: i64 = 1_715_508_000_000_000;

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

fn time(seconds: i64) -> SceneTime {
    SceneTime::Timestamp(MediaTime(NOON + seconds * 1_000_000))
}

fn observation(track_id: &str, timestamp: &str, class: Json) -> Json {
    json!({
        "track_id": track_id,
        "timestamp": timestamp,
        "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
        "class": class,
    })
}

#[test]
fn a_frame_keeps_the_time_box_class_and_colors_of_each_observation() {
    let timestamp = "2024-05-12T10:00:00Z";
    let car = json!({"type": "Car", "score": 0.8, "colors": [{"name": "Blue", "score": 0.7}]});
    let record =
        json!({"timestamp": timestamp, "observations": [observation("track-1", timestamp, car)]});
    // The wrapper form, behind a timestamp column and a tab.
    let wrapped = format!("{timestamp}\t{}", json!({"frame": record}));
    let (scenes, skipped) = decode("ADF_BETA_FRAME", &[record.to_string(), wrapped]);

    assert!(skipped.is_empty(), "{skipped:?}");
    for scene in &scenes {
        assert_eq!(scene.time().start(), time(0));
        let seen = &scene.entity("track-1").expect("the track").observations()[0];
        assert_eq!(seen.time, time(0));
        let bounds = seen.geometry.bounds();
        assert_eq!((bounds.left(), bounds.right()), (0.1, 0.4));
        let class = &seen.classifications[0];
        assert_eq!(
            (
                class.class_type.as_str(),
                class.score.map(scene::Score::value)
            ),
            ("car", Some(0.8))
        );
        let Some(Value::List(colors)) = class.attribute("vehicle_colors") else {
            panic!("vehicle colors");
        };
        assert_eq!(colors.len(), 1);
    }
}

#[test]
fn frames_join_in_one_scene_and_only_the_first_class_is_used() {
    let mut first = observation("a", "2024-05-12T10:00:00Z", Json::Null);
    first["class"] = Json::Null;
    first["classes"] = json!([
        {"type": "LicensePlate", "score": 0.9, "plate_number": "ABC123", "country_code": "SE"},
        {"type": "Car", "score": 0.5}
    ]);
    let record = json!({"frames": [
        {"timestamp": "2024-05-12T10:00:00Z", "observations": [first],
         "operations": [{"type": "DeleteOperation", "id": "gone"}]},
        {"timestamp": "2024-05-12T10:00:02Z",
         "observations": [observation("a", "2024-05-12T10:00:02Z", json!({"type": "Car"}))],
         "operations": [{"type": "RenameOperation", "from": "a", "to": "b"}]}
    ]});
    let (scenes, skipped) = decode("ADF_BETA_FRAME", &[record.to_string()]);

    assert!(skipped.is_empty(), "{skipped:?}");
    let [scene] = &scenes[..] else {
        panic!("one record, one scene");
    };
    assert_eq!(
        (scene.time().start(), scene.time().end()),
        (time(0), time(2))
    );
    let entity = scene
        .entity("a")
        .expect("both frames' observations are one entity");
    assert_eq!(entity.observations().len(), 2);
    let plate = &entity.observations()[0].classifications;
    assert_eq!(plate.len(), 1);
    assert_eq!(plate[0].class_type, "license_plate");
    assert_eq!(
        plate[0].attribute("plate_number"),
        Some(&Value::Str("ABC123".into()))
    );
    let labels: Vec<_> = scene.events().iter().map(Event::label).collect();
    assert_eq!(labels, ["Delete gone", "Rename a → b"]);
}

fn track(id: &str, at: &str, class: &str) -> Json {
    json!({
        "id": id,
        "classes": [{"type": class, "score": 0.95}],
        "observations": [{"timestamp": at, "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6}}],
        "start_time": at,
        "end_time": at,
        "duration": 0.0,
    })
}

#[test]
fn consolidated_tracks_are_scenes_by_time_and_lines_of_one_time_stay_apart_for_the_host_to_merge() {
    let mut bike = track("track-2", "2024-05-12T10:00:01Z", "Bike");
    bike["image"] = json!({"timestamp": "2024-05-12T10:00:01Z", "data": "aW1n",
        "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6}});
    bike["end_reason"] = json!("left the scene");
    let document = json!({"version": "1.0.0-beta1", "tracks": [
        track("track-1", "2024-05-12T10:00:00Z", "Car"),
        {"object": bike},
    ]});
    let lines = [
        document.to_string(),
        // The same moment in another line is another Scene of that time.
        format!(
            "2024-05-12T10:00:00Z\t{}",
            track("track-3", "2024-05-12T10:00:00Z", "Human")
        ),
        json!({"object": track("track-4", "2024-05-12T10:00:02Z", "Car")}).to_string(),
    ];
    let (scenes, skipped) = decode("ADF_BETA_CONSOLIDATED", &lines);

    assert!(skipped.is_empty(), "{skipped:?}");
    let seen: Vec<_> = scenes
        .iter()
        .map(|scene| {
            let ids: Vec<_> = scene
                .entities()
                .iter()
                .map(|e| e.id().to_string())
                .collect();
            (scene.time().start(), ids)
        })
        .collect();
    assert_eq!(
        seen,
        [
            (time(0), vec!["track-1".to_owned()]),
            (time(1), vec!["track-2".to_owned()]),
            (time(0), vec!["track-3".to_owned()]),
            (time(2), vec!["track-4".to_owned()]),
        ]
    );
    let bike = &scenes[1].entities()[0].observations()[0].classifications[0];
    assert_eq!(bike.class_type, "bike");
    let tracked = &scenes[1].entities()[0];
    assert_eq!(&*tracked.images[0].data, b"img");
    assert_eq!(tracked.end_reason.as_deref(), Some("left the scene"));
}

#[test]
fn a_bad_record_is_skipped_by_its_line_number_and_the_rest_are_read() {
    let good = json!({"timestamp": "2024-05-12T10:00:00Z", "observations": []});
    let mut no_box = observation("a", "2024-05-12T10:00:00Z", Json::Null);
    no_box
        .as_object_mut()
        .expect("an object")
        .remove("bounding_box");
    let lines = [
        good.to_string(),
        "{not json".to_owned(),
        json!({"observations": []}).to_string(),
        json!({"timestamp": "2024-05-12T10:00:00Z", "observations": [no_box]}).to_string(),
        good.to_string(),
    ];
    let (scenes, skipped) = decode("ADF_BETA_FRAME", &lines);
    assert_eq!(scenes.len(), 2);
    let positions: Vec<_> = skipped.iter().map(|(position, _)| *position).collect();
    assert_eq!(positions, [2, 3, 4]);

    let mut wrong_start = track("a", "2024-05-12T10:00:00Z", "Car");
    wrong_start["start_time"] = json!("2024-05-12T09:00:00Z");
    let (scenes, skipped) = decode("ADF_BETA_CONSOLIDATED", &[wrong_start.to_string()]);
    assert!(scenes.is_empty() && skipped.len() == 1);
}
