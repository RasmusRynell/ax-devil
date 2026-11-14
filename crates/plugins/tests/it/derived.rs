//! Derived lanes and tasks against real plugin processes: `input: lanes`, progress, and what a plugin is given as
//! written.

use crate::{
    common::{self, TestSink, decode},
    fixtures::{fixture, python_available},
};

use std::{fs, path::Path};

use foundation::{CancelToken, Interrupt};
use handler_api::{Bundle, BundleLane, Settings};
use plugins::{LoadedPlugin, Manifest, TaskProgress};
use scene::SceneTime;
use serde_json::json;

/// A lane file holding an empty sample at each of `frames`.
fn lane_file(folder: &Path, name: &str, frames: &[u32]) -> BundleLane {
    let mut text = String::from(r#"{"lane":{"basis":"frame","coverage":"sparse","metadata":{}}}"#);
    for frame in frames {
        text.push_str(&format!(
            "\n{{\"sample\":{{\"time\":{{\"start\":{{\"frame\":{frame}}},\"end\":{{\"frame\":{frame}}}}},\"entities\":[]}}}}"
        ));
    }
    let path = folder.join(name);
    fs::write(&path, text).expect("lane file");
    BundleLane {
        path,
        sha256: String::new(),
    }
}

fn bundle(lanes: Vec<(&str, BundleLane)>) -> Bundle {
    Bundle {
        lanes: lanes
            .into_iter()
            .map(|(key, lane)| (key.to_owned(), lane))
            .collect(),
        video: None,
    }
}

fn demo_manifest() -> std::path::PathBuf {
    fixture("derived-demo").join(plugins::MANIFEST_FILE)
}

#[test]
fn a_lanes_decode_reads_both_lane_files() {
    let plugin = LoadedPlugin::load(&demo_manifest()).expect("the demo loads");
    assert!(plugin.skipped.is_empty(), "{:?}", plugin.skipped);
    let decoder = &plugin.decoders[0];

    if !python_available() {
        return;
    }
    let folder = tempfile::tempdir().expect("folder");
    let truth = lane_file(folder.path(), "truth.jsonl", &[1, 2]);
    let model = lane_file(folder.path(), "model.jsonl", &[2, 3]);
    let path = folder.path().join("bundle.json");
    fs::write(
        &path,
        bundle(vec![("truth", truth), ("model", model)]).to_bytes(),
    )
    .expect("bundle");

    let run = decode(
        decoder,
        &path,
        &Settings::new(),
        &CancelToken::new(),
        TestSink::default(),
    );

    run.summary();
    let frames: Vec<_> = run
        .sink
        .samples
        .iter()
        .map(|(scene, _)| scene.time().start())
        .collect();
    let frame = SceneTime::Frame;
    assert_eq!(
        frames,
        [frame(1), frame(2), frame(3)],
        "{:?}",
        run.sink.skips
    );
}

#[test]
fn a_task_reports_progress_and_gets_a_setting_and_an_option_as_written() {
    if !python_available() {
        return;
    }
    let folder = tempfile::tempdir().expect("folder");
    let lane = lane_file(folder.path(), "lane.jsonl", &[1, 2]);
    let mut settings = Settings::new();
    settings.insert("label".to_owned(), "$HOME".into());
    let plugin = LoadedPlugin::new(Manifest::load(&demo_manifest()).expect("loads"), &settings)
        .expect("loads with settings");
    let task = &plugin.tasks[0];
    let mut options = Settings::new();
    options.insert("out".to_owned(), "$HOME".into());
    let options = task.spec().options.validate(&options).expect("options");

    let mut seen = Vec::new();
    task.run(
        &bundle(vec![("lane", lane)]),
        &options,
        &mut |progress| seen.push(progress),
        &CancelToken::new(),
    )
    .expect("the task succeeds");

    let progress = |done, text: &str| TaskProgress {
        done: Some(done),
        total: Some(2),
        text: Some(text.to_owned()),
    };
    // No `$NAME` in anything handed to a plugin is expanded: the program sees the text.
    assert_eq!(seen, [progress(0, "Counting"), progress(2, "$HOME|$HOME")]);
}

#[test]
fn a_task_whose_error_line_arrives_fails_with_its_reason() {
    let folder = tempfile::tempdir().expect("folder");
    let lane = lane_file(folder.path(), "lane.jsonl", &[1]);
    let script = r#"cat >/dev/null; printf '%s\n' '{"progress":{"done":1}}' '{"error":{"reason":"disk full"}}'"#;
    let manifest = json!({
        "manifest": 2, "id": "acme", "name": "ACME", "version": "1", "protocol": 3,
        "command": ["sh", "-c", script],
        "handlers": [{
            "kind": "task", "id": "acme_export", "name": "Export",
            "lanes": [{ "key": "lane", "label": "Lane" }]
        }]
    });
    let path = common::install(&folder.path().join("plugin"), &manifest);
    let plugin = LoadedPlugin::load(&path).expect("loads");
    let task = &plugin.tasks[0];
    let options = task
        .spec()
        .options
        .validate(&Settings::new())
        .expect("none");

    let mut seen = Vec::new();
    let outcome = task.run(
        &bundle(vec![("lane", lane)]),
        &options,
        &mut |progress| seen.push(progress),
        &CancelToken::new(),
    );

    assert_eq!(seen.len(), 1);
    match outcome {
        Err(Interrupt::Failed(error)) => assert_eq!(error.reason, "disk full"),
        other => panic!("the task fails: {other:?}"),
    }
}
