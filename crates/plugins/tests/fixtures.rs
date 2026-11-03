//! The sample plugins in `fixtures/plugins/`, run for real. They need `python3` and `sh`; without python3
//! the Python test says so and passes, unless `AX_DEVIL_REQUIRE_PYTHON` is set (CI sets it).
#![allow(clippy::print_stderr)] // the skip message is the test's only way to speak

mod common;

use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use common::{TestSink, decode};
use foundation::CancelToken;
use handler_api::{OnEqualKey, Settings};
use plugins::{LoadedPlugin, PluginDecoder};
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
    assert!(matches!(
        person.observations()[0].geometry,
        Geometry::Polygon(_)
    ));
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
