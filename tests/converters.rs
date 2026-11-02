//! Installed converters end to end: the command line installs, lists and checks them, and a lane over a video reads
//! the converter's samples to the end. The Python converter needs `python3`; without it that test says so and
//! passes, unless `AX_DEVIL_REQUIRE_PYTHON` is set (CI sets it).
#![allow(clippy::print_stderr)] // the skip message is the test's only way to speak

mod common;

use api_types::{
    handlers::PluginsInfo,
    playback::{
        BasisWire, ClientMessage, Direction, FRAME_MAP_NONE, IngestWire, LanePolicyWire,
        LaneStatusWire, MATCH_EXACT, ServerMessage, StickyWire,
    },
};
use ax_devil::plugins;
use common::{Backend, Event, fixture};
use converters::Registry;
use foundation::CancelToken;
use handlers::Handlers;
use playlist_resolvers::OverlayRef;
use scene_store::TrackRegistry;
use std::{
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    sync::Arc,
};

/// A backend that decodes with `handlers` and reports `installed`.
async fn started(handlers: Handlers, installed: PluginsInfo) -> Backend {
    Backend::start_with(false, handlers, |state| state.plugins = Arc::new(installed)).await
}

fn converter(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/converters")
        .join(name)
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

/// `ax-devil` with every user folder inside `home`, so the real registry and settings are never touched.
fn ax_devil(home: &Path, args: &[&std::ffi::OsStr]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .args(args)
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join("config"))
        .env("XDG_STATE_HOME", home.join("state"))
        .env("XDG_CACHE_HOME", home.join("cache"))
        .env("XDG_DATA_HOME", home.join("data"))
        .output()
        .unwrap()
}

/// Installs the fixture converter `name` into the registry under `home` without the command line.
fn install(home: &Path, name: &str) -> PathBuf {
    let file = home.join("data/ax-devil/plugins.json");
    let mut registry = Registry::read(&file).unwrap();
    registry.install(&converter(name), &[]).unwrap();
    registry.save().unwrap();
    file
}

#[test]
fn the_command_line_installs_lists_and_checks_a_converter_and_fails_a_check_that_proves_nothing() {
    if !python_available() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let run = |args: &[&std::ffi::OsStr]| ax_devil(home.path(), args);
    let text = |bytes: &[u8]| String::from_utf8_lossy(bytes).into_owned();
    let tracks = converter("python-tracks");
    let sample = tracks.join("sample.tracks.csv");
    let empty = home.path().join("empty.tracks.csv");
    std::fs::write(&empty, "").unwrap();

    let installed = run(&["plugins".as_ref(), "install".as_ref(), tracks.as_ref()]);
    assert!(installed.status.success(), "{}", text(&installed.stderr));
    let listed = run(&["list-handlers".as_ref()]);
    assert!(text(&listed.stdout).contains("python-tracks"));
    assert!(text(&run(&["plugins".as_ref(), "list".as_ref()]).stdout).contains("python-tracks"));

    let checked = run(&[
        "plugins".as_ref(),
        "check".as_ref(),
        "python-tracks".as_ref(),
        sample.as_ref(),
        "--option".as_ref(),
        "camera=rear".as_ref(),
    ]);
    assert!(checked.status.success(), "{}", text(&checked.stderr));
    assert!(
        text(&checked.stdout).contains("4 samples"),
        "{}",
        text(&checked.stdout)
    );
    for (id, file) in [("python-tracks", &empty), ("no-such-converter", &sample)] {
        let failed = run(&[
            "plugins".as_ref(),
            "check".as_ref(),
            id.as_ref(),
            file.as_ref(),
        ]);
        assert_eq!(failed.status.code(), Some(1), "{id}");
    }

    let removed = run(&[
        "plugins".as_ref(),
        "remove".as_ref(),
        "python-tracks".as_ref(),
    ]);
    assert!(removed.status.success());
    assert!(!text(&run(&["list-handlers".as_ref()]).stdout).contains("python-tracks"));
}

#[tokio::test]
async fn a_lane_over_a_converters_file_gets_its_samples_and_ends_complete() {
    if !python_available() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let registry = install(home.path(), "python-tracks");
    let (handlers, installed) = plugins::load(&Ok(registry), Handlers::built_in());
    let backend = started(handlers, installed).await;
    let added = backend
        .add_with_overlays(
            &fixture("cfr_h264_gop10_bf2.mp4"),
            &[converter("python-tracks/sample.tracks.csv")],
        )
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;

    let (samples, ingest) = loop {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                lane: 0,
                status:
                    LaneStatusWire::Ready {
                        samples, ingest, ..
                    },
            }) if ingest != IngestWire::Reading => break (samples, ingest),
            Event::Message(ServerMessage::LaneStatus {
                status: LaneStatusWire::Failed(error),
                ..
            }) => panic!("the lane failed: {error:?}"),
            _ => {}
        }
    };

    assert_eq!(ingest, IngestWire::Complete);
    assert_eq!(samples, 4);
}

#[test]
fn a_converter_that_writes_out_of_order_has_its_samples_sorted_and_the_replaced_one_dropped() {
    let home = tempfile::tempdir().unwrap();
    let registry = install(home.path(), "shell-times");
    let (handlers, _) = plugins::load(&Ok(registry), Handlers::built_in());
    let overlay = OverlayRef {
        label: "times".into(),
        path: converter("shell-times/sample.times.txt"),
        handler: "shell-times".into(),
        options: Default::default(),
    };

    let track = TrackRegistry::new(handlers, None)
        .open(&overlay, &CancelToken::new())
        .unwrap();
    let snapshot = loop {
        let snapshot = track.snapshot();
        if snapshot.ingest != scene_store::Ingest::Reading {
            break snapshot;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    };

    assert_eq!(snapshot.ingest, scene_store::Ingest::Complete);
    assert_eq!(snapshot.keys.len(), 5);
    let ids = |index: usize| -> Vec<String> {
        let scene = track.scene_in(&snapshot, index).unwrap();
        scene
            .entities()
            .iter()
            .map(|entity| entity.id().as_str().to_owned())
            .collect()
    };
    let starts: Vec<_> = (0..5)
        .map(|index| track.scene_in(&snapshot, index).unwrap().time().start())
        .collect();
    assert!(
        starts.windows(2).all(|pair| pair[0] < pair[1]),
        "{starts:?}"
    );
    // The key at 100 ms held object 1 until the replace dropped it for object 4.
    assert_eq!(ids(1), ["4"]);
}

#[tokio::test]
async fn the_plugins_route_lists_the_converters_that_loaded_and_the_one_that_did_not() {
    let home = tempfile::tempdir().unwrap();
    let registry = install(home.path(), "shell-times");
    let gone = home.path().join("gone/ax-devil-converter.json");
    let text = std::fs::read_to_string(&registry).unwrap();
    let mut document: serde_json::Value = serde_json::from_str(&text).unwrap();
    document["converters"]
        .as_array_mut()
        .unwrap()
        .push(serde_json::json!({ "manifest": gone }));
    std::fs::write(&registry, document.to_string()).unwrap();
    let (handlers, installed) = plugins::load(&Ok(registry), Handlers::built_in());
    let backend = started(handlers, installed).await;

    let plugins = get(&backend, "/api/plugins").await;
    let [loaded, failed] = &plugins["converters"].as_array().unwrap()[..] else {
        panic!("{plugins}");
    };
    assert_eq!(loaded["status"]["state"], "Loaded");
    assert_eq!(loaded["status"]["id"], "shell-times");
    assert_eq!(failed["status"]["state"], "Failed");
    assert!(
        failed["status"]["reason"]
            .as_str()
            .unwrap()
            .contains("gone")
    );

    let handlers = get(&backend, "/api/handlers").await;
    let plugin_of = |handler: &str| {
        let decoders = handlers["decoders"].as_array().unwrap();
        let decoder = decoders.iter().find(|d| d["handler"] == handler).unwrap();
        decoder.get("plugin").cloned()
    };
    assert_eq!(plugin_of("shell-times"), Some("shell-times".into()));
    assert_eq!(plugin_of("MOT_FILE"), None);
}

/// The JSON body of a GET to the local backend.
async fn get(backend: &Backend, path: &str) -> serde_json::Value {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut socket = tokio::net::TcpStream::connect(backend.address)
        .await
        .unwrap();
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        backend.address
    );
    socket.write_all(request.as_bytes()).await.unwrap();
    let mut response = String::new();
    socket.read_to_string(&mut response).await.unwrap();
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    let (_, body) = response.split_once("\r\n\r\n").unwrap();
    serde_json::from_str(body).unwrap_or_else(|error| panic!("{error}: {body}"))
}

#[tokio::test]
async fn a_lane_over_a_converter_that_defaults_to_time_is_matched_by_time_without_being_told() {
    let home = tempfile::tempdir().unwrap();
    let registry = install(home.path(), "shell-times");
    let (handlers, installed) = plugins::load(&Ok(registry), Handlers::built_in());
    let backend = started(handlers, installed).await;
    // `.txt` is also a MOT file, so the converter is named.
    let overlays = [workspace::intake::NewOverlay {
        path: converter("shell-times/sample.times.txt"),
        handler: Some("shell-times".into()),
        label: None,
    }];
    let item = workspace::intake::video(
        &fixture("cfr_h264_gop10_bf2.mp4"),
        &overlays,
        &backend.state.handlers,
    )
    .unwrap();
    let added = backend.add_item(item).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;
    viewer.want(0, 5, 0, Direction::Forward).await;
    viewer
        .send(&ClientMessage::LanePolicy {
            lane: 0,
            revision: 1,
            policy: LanePolicyWire {
                basis: None,
                tolerance_ms: Some(10),
                sticky: StickyWire {
                    enabled: false,
                    timeout_ms: None,
                },
            },
            subscribed: true,
        })
        .await;

    // The records are at 0, 100, 200, 300 and 400 ms: by time, frame 3 (100 ms) shows the second and frame 1 none;
    // by frame number, frame 1 would show the second.
    let (mut bases, mut cells) = (None, std::collections::BTreeMap::new());
    while cells.len() < 6 {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                status:
                    LaneStatusWire::Ready {
                        bases: offered,
                        default_basis,
                        ..
                    },
                ..
            }) => bases = Some((offered, default_basis)),
            Event::Map(map) => {
                for (k, cell) in map.samples.iter().enumerate() {
                    cells.insert(map.first + k as u64, (*cell, map.kinds[k]));
                }
            }
            _ => {}
        }
    }
    assert_eq!(
        bases,
        Some((vec![BasisWire::Time, BasisWire::Frame], BasisWire::Time))
    );
    assert_eq!(cells[&1].0, FRAME_MAP_NONE);
    assert_eq!(cells[&3], (1, MATCH_EXACT));
}
