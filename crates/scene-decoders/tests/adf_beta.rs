//! The ADF beta decoders, through the registry as the host uses it.

use foundation::{CancelToken, Interrupt, MediaTime};
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
    // Beta frames are plain JSON: the wrapper form reads, a timestamp column and a tab before it does not.
    let wrapped = json!({"frame": record}).to_string();
    let tsv = format!("{timestamp}\t{wrapped}");
    let (scenes, skipped) = decode("ADF_BETA_FRAME", &[record.to_string(), wrapped, tsv]);

    assert_eq!(skipped.len(), 1);
    assert_eq!(skipped[0].0, 3);
    assert!(skipped[0].1.contains("invalid JSON"), "{skipped:?}");
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

#[test]
fn beta_names_classes_by_exact_spelling_and_gives_them_only_its_own_details() {
    let at = "2024-05-12T10:00:00Z";
    let blue = json!([{"name": "Blue", "score": 0.7}]);
    let seen = |class: Json| {
        let line = json!({"timestamp": at, "observations": [observation("a", at, class)]});
        let (scenes, skipped) = decode("ADF_BETA_FRAME", &[line.to_string()]);
        (
            scenes
                .first()
                .map(|scene| scene.entities()[0].observations()[0].clone()),
            skipped,
        )
    };
    let classes = |class: Json| seen(class).0.expect("read").classifications;

    let person = &classes(json!({"type": "Person", "upper_clothing_colors": blue}))[0];
    assert_eq!(person.class_type, "person");
    assert!(person.attribute("upper_clothing_colors").is_none());
    let human = &classes(json!({"type": "Human", "upper_clothing_colors": blue}))[0];
    assert_eq!(human.class_type, "human");
    assert!(human.attribute("upper_clothing_colors").is_some());
    let plate =
        &classes(json!({"type": "LicensePlate", "state_code": "CA", "plate_number": "ABC"}))[0];
    assert_eq!(plate.class_type, "license_plate");
    assert!(plate.attribute("state_code").is_none() && plate.attribute("plate_number").is_some());
    assert_eq!(
        classes(json!({"type": "Licenseplate"}))[0].class_type,
        "licenseplate"
    );
    assert!(
        classes(json!({"type": "Bike", "colors": blue}))[0]
            .attribute("vehicle_colors")
            .is_none()
    );
    // A false type is no class; the object is still shown. A missing type rejects the record.
    for kind in [json!(null), json!(""), json!(0)] {
        assert!(classes(json!({"type": kind, "score": 0.5})).is_empty());
    }
    assert!(seen(json!({"score": 0.5})).0.is_none());
}

/// A file on disk, as opposed to the live source `common::Memory` stands for.
struct OnDisk<'a>(common::Memory<'a>);

impl handler_api::Input for OnDisk<'_> {
    fn path(&self) -> Option<&std::path::Path> {
        Some(std::path::Path::new("beta.jsonl"))
    }

    fn next(
        &mut self,
        cancel: &CancelToken,
    ) -> Result<Option<handler_api::Record<'_>>, foundation::Interrupt<handler_api::DecodeError>>
    {
        self.0.next(cancel)
    }
}

#[test]
fn frames_at_different_times_and_world_velocity_are_warned_about_once_a_file_and_once_a_kind_live()
{
    let at = |seconds: u32| format!("2024-05-12T10:00:0{seconds}Z");
    let mut moving = observation("a", &at(0), Json::Null);
    moving["world_velocity"] = json!({"x": 1.0});
    let frame = |seconds, observations: Vec<Json>| json!({"timestamp": at(seconds), "observations": observations});
    let lines = [
        json!({"frames": [frame(0, vec![moving.clone()]), frame(2, vec![])]}).to_string(),
        frame(0, vec![moving]).to_string(),
        frame(0, vec![]).to_string(),
    ]
    .join("\n");
    let decoder = common::decoder("ADF_BETA_FRAME");
    let run = |input: &mut dyn handler_api::Input| {
        let (gave, result) =
            common::run_input(&*decoder, input, &Settings::new(), &CancelToken::new());
        let problems = |warnings: Vec<foundation::Warning>| -> Vec<String> {
            warnings
                .into_iter()
                .map(|warning| warning.problem)
                .collect()
        };
        (
            problems(gave.warnings),
            problems(result.expect("decoded").warnings),
        )
    };

    let (sunk, summarized) = run(&mut OnDisk(common::lines(lines.as_bytes())));
    assert!(sunk.is_empty());
    assert_eq!(
        summarized,
        [
            "Records with frames at different times: 1",
            "Observations with a world velocity: 2"
        ]
    );
    let (sunk, summarized) = run(&mut common::lines(lines.as_bytes()));
    assert_eq!(
        sunk,
        [
            "Records with frames at different times: 1",
            "Observations with a world velocity: 1"
        ]
    );
    assert!(summarized.is_empty());
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
        track("track-3", "2024-05-12T10:00:00Z", "Human").to_string(),
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
}

#[test]
fn a_bad_beta_track_fails_the_whole_file_and_of_one_id_the_last_track_wins() {
    let at = "2024-05-12T10:00:00Z";
    let good = track("a", at, "Car");
    let with = |key: &str, value: Json| {
        let mut changed = good.clone();
        changed[key] = value;
        changed
    };
    let cases = [
        (json!({"version": "1.0.0", "tracks": [good]}), "version"),
        (with("duration", Json::Null), "duration"),
        (with("duration", json!("soon")), "duration"),
        (with("duration", json!(5.0)), "duration"),
        (with("id", json!(0)), "id"),
        (
            with("start_time", json!("2024-05-12T09:00:00Z")),
            "start_time",
        ),
        // Beta takes no timestamp column.
        (json!(format!("{at}\t{good}")), "invalid JSON"),
    ];
    for (line, wanted) in cases {
        let line = line
            .as_str()
            .map_or_else(|| line.to_string(), str::to_owned);
        let reason = failure("ADF_BETA_CONSOLIDATED", &[good.to_string(), line]);
        assert!(
            reason.starts_with("Line 2: ") && reason.contains(wanted),
            "{reason}"
        );
    }

    let document = json!({"version": "1.0.0-beta1", "tracks": [
        track("a", at, "Car"),
        with("id", json!("0")),
        {"object": track("a", "2024-05-12T10:00:01Z", "Bus")},
    ]});
    let (scenes, _) = decode("ADF_BETA_CONSOLIDATED", &[document.to_string()]);
    let seen: Vec<_> = scenes
        .iter()
        .map(|scene| (scene.time().start(), scene.entities()[0].id().to_string()))
        .collect();
    assert_eq!(seen, [(time(0), "0".to_owned()), (time(1), "a".to_owned())]);
}

#[test]
fn a_beta_track_class_gets_the_details_of_what_its_name_becomes() {
    let at = "2024-05-12T10:00:00Z";
    let blue = json!([{"name": "Blue", "score": 0.7}]);
    let mut one = track("a", at, "Car");
    one["classes"] = json!([
        {"type": "human", "upper_clothing_colors": blue, "carries_bag": true},
        {"type": "VehicleOther", "colors": blue},
        {"type": "Bike", "colors": blue},
    ]);
    let (scenes, _) = decode("ADF_BETA_CONSOLIDATED", &[one.to_string()]);

    let classes = &scenes[0].entities()[0].observations()[0].classifications;
    assert_eq!(classes[0].class_type, "human");
    assert!(classes[0].attribute("upper_clothing_colors").is_some());
    assert!(classes[0].attribute("carries_bag").is_none());
    assert_eq!(classes[1].class_type, "vehicle_other");
    assert!(classes[1].attribute("vehicle_colors").is_some());
    assert!(classes[2].attribute("vehicle_colors").is_none());
}
