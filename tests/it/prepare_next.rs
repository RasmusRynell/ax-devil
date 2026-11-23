//! Preparing the next playlist entry: a viewer that names it has its video's frame index and its overlay's artifact
//! built into the cache folder while it plays the entry before, when the setting allows it, and then only.

use crate::common::{Backend, WAIT, fixture, mot_options};
use api_types::playback::ClientMessage;
use serde_json::json;
use std::{fs, time::Duration};

/// The files in `folder` of the cache with this extension.
fn cached(backend: &Backend, folder: &str, extension: &str) -> Vec<String> {
    let Ok(entries) = fs::read_dir(backend.folder.path().join("cache").join(folder)) else {
        return Vec::new();
    };
    entries
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|name| name.ends_with(extension))
        .collect()
}

#[tokio::test]
async fn a_named_next_entry_is_built_into_the_cache_once_the_setting_is_on() {
    let backend = Backend::start().await;
    let (videos, overlays) = (
        backend.folder.path().join("videos"),
        backend.folder.path().join("overlays"),
    );
    fs::create_dir_all(&videos).unwrap();
    fs::create_dir_all(&overlays).unwrap();
    for name in ["a", "b"] {
        fs::copy(
            fixture("cfr_h264_gop10_bf2.mp4"),
            videos.join(format!("{name}.mp4")),
        )
        .unwrap();
        fs::write(
            overlays.join(format!("{name}.txt")),
            "1,1,10,10,20,20,1,1,1\n",
        )
        .unwrap();
    }
    let settings = json!({
        "videos_dir": videos,
        "overlays_dir": overlays,
        "handler_type": "MOT_FILE",
        "options": mot_options((1920, 1080)),
    });
    let playlist = backend
        .add_playlist("folder_pair", settings.as_object().unwrap().clone())
        .await;
    let entries = playlist.content.entries();
    let mut viewer = backend.viewer().await;
    viewer
        .open(&playlist.ticket_for(&entries[0].id), None)
        .await;
    viewer.opened().await;

    let ask = ClientMessage::Prepare {
        entry: Some(entries[1].id.as_str().to_owned()),
        lanes: vec!["b".into()],
    };
    // With the setting off the wish is kept and nothing is built.
    viewer.send(&ask).await;
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(cached(&backend, "frame-index", ".axidx").len(), 1);

    // Turning it on applies to the wish already made.
    backend
        .state
        .settings
        .update("playback.prepare_next_video", |_| Some(true.into()))
        .unwrap();
    tokio::time::timeout(WAIT, async {
        while cached(&backend, "frame-index", ".axidx").len() < 2
            || cached(&backend, "scenes", ".axscene").len() < 2
        {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("the next entry's index and overlay artifact are built");
    let indexes = cached(&backend, "frame-index", ".axidx");
    assert!(
        indexes.iter().any(|name| name.starts_with("b.mp4-")),
        "{indexes:?}"
    );
}
