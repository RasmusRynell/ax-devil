//! The sample plugins in `fixtures/plugins/`, run for real. They need `python3` and `sh`; without python3
//! the Python test says so and passes, unless `AX_DEVIL_REQUIRE_PYTHON` is set (CI sets it).
#![allow(clippy::print_stderr)] // the skip message is the test's only way to speak

use crate::common;

use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

use common::{Scripted, TestSink, decode, decode_records};
use foundation::{CancelToken, Interrupt, Location, UserError};
use handler_api::{Decoder, OnEqualKey, PlaylistResolver, Settings, Values, VideoRef};
use plugins::{LoadedPlugin, PluginDecoder, PluginResolver};
use scene::{Coverage, Geometry, Operation, SceneTime, TimeKind, Value};
use std::sync::Arc;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../fixtures/plugins")
        .join(name)
}

fn decoder(name: &str) -> Arc<PluginDecoder> {
    LoadedPlugin::load(&fixture(name).join("ax-devil-plugin.json"))
        .expect("the fixture manifest loads")
        .decoders
        .remove(0)
}

fn resolver(name: &str) -> Arc<PluginResolver> {
    LoadedPlugin::load(&fixture(name).join("ax-devil-plugin.json"))
        .expect("the fixture manifest loads")
        .resolvers
        .remove(0)
}

/// `settings` checked against the resolver's form, as the host does before it scans or resolves.
fn values(resolver: &PluginResolver, settings: serde_json::Value) -> Values {
    let serde_json::Value::Object(settings) = settings else {
        panic!("settings are an object");
    };
    resolver
        .spec()
        .form
        .validate(&settings)
        .expect("the settings fit")
}

fn python_available() -> bool {
    let found = Command::new("python3")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    if !found {
        assert!(
            std::env::var_os("AX_DEVIL_REQUIRE_PYTHON").is_none(),
            "python3 is required (AX_DEVIL_REQUIRE_PYTHON is set) but was not found on PATH"
        );
        eprintln!(
            "skipped: python3 is not on PATH; set AX_DEVIL_REQUIRE_PYTHON to make that a failure"
        );
    }
    found
}

fn key(scene: &scene::Scene) -> SceneTime {
    scene.time().start()
}

#[test]
fn a_plugin_keeps_an_observation_on_another_clock() {
    let folder = tempfile::tempdir().expect("folder");
    let line = r#"{"sample":{"time":{"start":{"frame":3},"end":{"frame":3}},"entities":[{"id":"a","observations":[{"time":{"us":100},"geometry":{"box":[0.1,0.2,0.3,0.4]}}]}]}}"#;
    let command = serde_json::json!([
        "sh",
        "-c",
        format!("cat >/dev/null; printf '%s\\n' '{line}'")
    ]);
    let manifest = common::manifest_text(&command.to_string(), "");
    let path = folder.path().join(plugins::MANIFEST_FILE);
    std::fs::write(&path, manifest).expect("manifest");
    let decoder = LoadedPlugin::load(&path).expect("loads").decoders.remove(0);
    let run = decode(
        &decoder,
        &folder.path().join("input.test"),
        &Settings::new(),
        &CancelToken::new(),
        TestSink::default(),
    );
    run.summary();
    assert!(run.sink.skips.is_empty());
    let scene = &run.sink.samples[0].0;
    assert_eq!(scene.time().start(), SceneTime::Frame(3));
    let observation = scene.entities()[0].latest_observation().expect("retained");
    assert_eq!(observation.time.frame(), None);
    assert_eq!(
        observation.time.timestamp(),
        Some(foundation::MediaTime(100))
    );
    let item = scene::serial::columns::encode_sample_item(
        scene,
        &scene::FilterPolicy {
            name: "test",
            options: Vec::new(),
        },
    )
    .expect("display and export sample");
    // The display/export encoder must retain the entity as one drawable row.
    assert_eq!(u32::from_le_bytes(item[8..12].try_into().unwrap()), 1);
}

#[test]
fn the_python_plugin_gives_the_model_everything_it_covers() {
    if !python_available() {
        return;
    }
    let mut options = Settings::new();
    options.insert("camera".to_owned(), "rear".into());
    options.insert("not_declared".to_owned(), "x".into());
    let run = decode(
        &decoder("python-tracks"),
        &fixture("python-tracks/sample.tracks.csv"),
        &options,
        &CancelToken::new(),
        TestSink::default(),
    );
    let summary = run.summary();

    assert_eq!(summary.coverage, Coverage::Dense);
    assert_eq!(summary.filter.options.len(), 2);
    assert_eq!(summary.metadata[1].1, Value::Str("rear".into()));
    let frames: Vec<SceneTime> = run
        .sink
        .samples
        .iter()
        .map(|(scene, _)| key(scene))
        .collect();
    let frame = SceneTime::Frame;
    assert_eq!(frames, [frame(0), frame(1), frame(1), frame(2), frame(3)]);
    assert!(
        run.sink
            .samples
            .iter()
            .all(|(_, mode)| *mode == OnEqualKey::Merge)
    );

    // Pixels were normalised by the plugin's width and height options (defaults 1920 and 1080).
    let car = run.sink.samples[0].0.entity("1").expect("entity 1");
    let observation = &car.observations()[0];
    let Geometry::Box(bounds) = &observation.geometry else {
        panic!("a box")
    };
    assert!((bounds.left() - 0.1).abs() < 1e-9 && (bounds.bottom() - 0.4).abs() < 1e-9);
    let class = &observation.classifications[0];
    assert!(class.score.is_some() && class.confidence.is_some());
    assert!(class.attribute("color_list").is_some());
    assert!(observation.measurements.is_some());

    let person = run.sink.samples[2].0.entity("2").expect("entity 2");
    assert_eq!(
        person.observations()[0]
            .geometry
            .polygon_points()
            .unwrap()
            .len(),
        3
    );
    assert!(
        person.observations()[0].classifications[0]
            .confidence
            .is_none()
    );
    let renamed = run.sink.samples[4].0.events();
    assert!(
        matches!(&renamed[0].operation, Operation::Rename { from, to } if from == "1" && to == "9")
    );

    // A malformed row and a truncated one are skipped; neither ends the plugin.
    let skips = &run.sink.skips;
    assert_eq!(skips.len(), 2, "{skips:?}");
    assert!(skips[0].1.starts_with("row 6:"), "{skips:?}");
    assert!(skips[1].1.starts_with("row 8:"), "{skips:?}");
}

#[test]
fn the_shell_plugin_emits_times_out_of_order_and_a_replace() {
    let run = decode(
        &decoder("shell-times"),
        &fixture("shell-times/sample.times.txt"),
        &Settings::new(),
        &CancelToken::new(),
        TestSink::default(),
    );
    let summary = run.summary();

    assert!(run.sink.header.is_none() && summary.filter.options.is_empty());
    let us: Vec<i64> = run
        .sink
        .samples
        .iter()
        .map(|(scene, _)| match key(scene) {
            SceneTime::Timestamp(time) => time.0,
            SceneTime::Frame(_) => panic!("a time-keyed sample"),
        })
        .collect();
    assert_eq!(us, [0, 100_000, 300_000, 200_000, 100_000, 400_000]);
    assert_eq!(key(&run.sink.samples[0].0).kind(), TimeKind::Timestamp);
    let modes: Vec<OnEqualKey> = run.sink.samples.iter().map(|(_, mode)| *mode).collect();
    assert_eq!(modes[4], OnEqualKey::Replace);
    assert_eq!(
        modes
            .iter()
            .filter(|mode| **mode == OnEqualKey::Merge)
            .count(),
        5
    );
    assert!(run.sink.warnings.is_empty());
}

#[test]
fn the_shell_records_plugin_answers_each_record_of_a_live_source_with_one_sample() {
    let records = decoder("shell-records");
    assert!(records.reads_live() && !decoder("shell-times").reads_live());
    let mut live = Scripted::new(
        &[
            b"100000 7 car 0.1 0.2 0.3 0.4",
            b"not a record",
            &[0xff, 0xfe],
            "\u{3000}200000 7 car 0.15 0.2 0.35 0.4\u{a0}".as_bytes(),
            "\u{a0} \t".as_bytes(),
        ],
        Duration::ZERO,
    );

    let run = decode_records(&records, &mut live, &CancelToken::new());

    run.summary();
    let us: Vec<i64> = run
        .sink
        .samples
        .iter()
        .map(|(scene, _)| match key(scene) {
            SceneTime::Timestamp(time) => time.0,
            SceneTime::Frame(_) => panic!("a time-keyed sample"),
        })
        .collect();
    assert_eq!(us, [100_000, 200_000]);
    let skipped: Vec<u64> = run
        .sink
        .skips
        .iter()
        .map(|(position, _)| *position)
        .collect();
    assert_eq!(skipped, [2, 3]);
}

#[test]
fn the_shell_plugin_reads_a_file_whose_name_has_a_quote_and_a_backslash() {
    // The request carries the path as a JSON string, so the quote and the backslash arrive escaped.
    let folder = tempfile::tempdir().expect("a temporary folder");
    let file = folder.path().join(r#"a"b\c.times.txt"#);
    std::fs::copy(fixture("shell-times/sample.times.txt"), &file).expect("the copy");

    let run = decode(
        &decoder("shell-times"),
        &file,
        &Settings::new(),
        &CancelToken::new(),
        TestSink::default(),
    );

    assert_eq!(run.sink.samples.len(), 6);
    assert!(run.sink.warnings.is_empty() && run.sink.skips.is_empty());
}

#[test]
fn the_python_resolver_scans_run_folders_and_resolves_the_chosen_ones_into_entries() {
    if !python_available() {
        return;
    }
    let root = tempfile::tempdir().expect("a temporary folder");
    for run in ["run-a", "run-b"] {
        std::fs::create_dir(root.path().join(run)).expect("run folder");
    }
    std::fs::write(root.path().join("run-a/gt.txt"), "").expect("ground truth");
    let resolver = resolver("python-runs");
    let settings = |extra: serde_json::Value| {
        let mut settings = serde_json::json!({ "root": root.path() });
        settings
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        values(&resolver, settings)
    };

    let choices = resolver
        .scan(
            "runs",
            &settings(serde_json::json!({})),
            &CancelToken::new(),
        )
        .expect("scan");
    let found: Vec<_> = choices
        .iter()
        .map(|c| (c.value.as_str(), c.label.as_str(), c.detail.as_str()))
        .collect();
    assert_eq!(
        found,
        [("run-a", "run-a", "1 files"), ("run-b", "run-b", "0 files")]
    );

    let all = resolver
        .resolve(&settings(serde_json::json!({})), &CancelToken::new())
        .expect("resolve");
    let folder = root.path().file_name().unwrap().to_str().unwrap();
    let [playlist] = all.playlists.as_slice() else {
        panic!("one playlist");
    };
    assert_eq!(playlist.name, folder);
    let names: Vec<_> = playlist.entries.iter().map(|e| e.name.as_str()).collect();
    assert_eq!(names, ["run-a", "run-b"]);
    assert_eq!(
        playlist.entries[0].video,
        VideoRef::File(root.path().join("run-a/video.mp4"))
    );
    let overlay = &playlist.entries[0].overlays[0];
    assert_eq!(
        (overlay.slot.as_str(), overlay.handler.as_str()),
        ("gt", "MOT_FILE")
    );
    assert!(playlist.entries[1].overlays.is_empty());
    assert_eq!(all.warnings.len(), 1, "run-b has no gt.txt");

    let chosen = resolver
        .resolve(
            &settings(serde_json::json!({ "runs": ["run-b"] })),
            &CancelToken::new(),
        )
        .expect("resolve");
    assert_eq!(chosen.playlists[0].entries.len(), 1);
}

#[test]
fn a_reported_error_names_the_setting_it_is_about() {
    if !python_available() {
        return;
    }
    let resolver = resolver("python-runs");
    let missing = values(&resolver, serde_json::json!({ "root": "/no/such/folder" }));
    let Err(Interrupt::Failed(error)) = resolver.resolve(&missing, &CancelToken::new()) else {
        panic!("a missing folder fails the resolve");
    };
    assert_eq!(error.location, Some(Location::field("root")));
    assert_eq!(error.reason, "Not a folder");
}

/// A resolver whose program is `script` run by `sh -c`, which prints what it likes.
fn scripted(folder: &Path, script: &str) -> Arc<PluginResolver> {
    let manifest = serde_json::json!({
        "manifest": 2, "id": "scripted", "name": "Scripted", "version": "1", "protocol": 3,
        "command": ["sh", "-c", script], "code": ["ax-devil-plugin.json"],
        "handlers": [{
            "kind": "playlist_resolver", "id": "scripted", "name": "Scripted", "description": "Prints",
            "settings": [
                { "key": "root", "label": "Root", "kind": "folder" },
                { "key": "runs", "label": "Runs", "kind": "scan", "depends_on": ["root"] }
            ]
        }]
    });
    let path = folder.join("ax-devil-plugin.json");
    std::fs::write(&path, manifest.to_string()).expect("manifest");
    LoadedPlugin::load(&path)
        .expect("loads")
        .resolvers
        .remove(0)
}

fn failure(resolver: &PluginResolver) -> UserError {
    match resolver.resolve(
        &values(resolver, serde_json::json!({})),
        &CancelToken::new(),
    ) {
        Err(Interrupt::Failed(error)) => error,
        _ => panic!("the resolve fails"),
    }
}

#[test]
fn a_resolve_that_breaks_the_rules_fails_naming_the_line_and_a_failing_plugin_leaves_no_playlist() {
    let folder = tempfile::tempdir().expect("folder");
    let entry = |name: &str, video: &str| {
        format!(r#"{{"entry":{{"name":"{name}","video":{{"file":"{video}"}}}}}}"#)
    };
    let print = |lines: &[String], exit: i32| {
        let script = format!(
            "cat >/dev/null; printf '%s\\n' '{}'; exit {exit}",
            lines.join("' '")
        );
        scripted(folder.path(), &script)
    };

    let relative = failure(&print(&[entry("a", "v/1.mp4")], 0));
    assert!(
        relative.reason.starts_with("line 1:") && relative.reason.contains("absolute"),
        "{}",
        relative.reason
    );
    let nameless = failure(&print(&[entry("", "/v/1.mp4")], 0));
    assert!(
        nameless.reason.starts_with("line 1:") && nameless.reason.contains("empty"),
        "{}",
        nameless.reason
    );
    let halfway = failure(&print(&[entry("a", "/v/1.mp4")], 3));
    assert!(halfway.reason.contains("exit code 3"), "{}", halfway.reason);
}

#[test]
fn cancelling_stops_a_scan_that_is_still_running() {
    let folder = tempfile::tempdir().expect("folder");
    let resolver = scripted(folder.path(), "sleep 60");
    let cancel = CancelToken::new();
    let stopper = cancel.clone();
    let thread = std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(200));
        stopper.cancel();
    });

    let started = std::time::Instant::now();
    let result = resolver.scan("runs", &values(&resolver, serde_json::json!({})), &cancel);
    thread.join().expect("the stopper");

    assert!(matches!(result, Err(Interrupt::Cancelled)));
    assert!(started.elapsed() < std::time::Duration::from_secs(10));
}
