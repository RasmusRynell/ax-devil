//! A change to the files of an item: an open viewer keeps its stream and is told, and the runner's records find it.

use crate::{GRACE, Rig, authority, stream};
use handler_api::Settings;
use handlers::{HandlerSet, Handlers};
use playback::Authority;
use sessions::{
    CursorOptions,
    session::{Command, OnRuntime, Service, Sources},
};
use std::{fs, sync::Arc, time::Duration};
use workspace::intake::{NewOverlay, video};
use workspace_store::{DocCommand, Resolution};

#[tokio::test(start_paused = true)]
async fn an_open_viewer_keeps_its_stream_when_its_files_change_until_the_item_resolves_again() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    let (item, key) = rig.open_video(&owner, "a.mp4");
    let lease = stream(&rig.service, &owner, &key).unwrap();

    rig.service.source_changed(item);
    assert_eq!(owner.state().doc.workspace.changed[&item], 1);
    assert!(
        rig.jobs.take().is_empty(),
        "nothing resolves again by itself"
    );
    assert!(matches!(authority(&lease), Authority::Own { .. }));

    owner
        .command(Command::Content(DocCommand::Resolve(item)))
        .unwrap();
    assert_eq!(authority(&lease), Authority::Revoked);
    assert!(owner.state().doc.workspace.changed.is_empty());
}

#[tokio::test]
async fn the_files_an_item_was_resolved_from_are_found_changed_by_the_next_looks() {
    let dir = tempfile::tempdir().unwrap();
    let (video_file, overlay_file) = (dir.path().join("a.mp4"), dir.path().join("gt.txt"));
    fs::write(&video_file, "x").unwrap();
    fs::write(&overlay_file, "").unwrap();
    let overlay = NewOverlay {
        path: overlay_file.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    let item = video(&video_file, &[overlay], &Handlers::built_in()).unwrap();
    let id = item.id;

    let sources = Arc::new(Sources::default());
    let service: Service<u8> = Service::new(
        GRACE,
        Arc::new(OnRuntime {
            runtime: tokio::runtime::Handle::current(),
            handlers: HandlerSet::new(Handlers::built_in()),
            environment: Arc::new(workspace::Environment::of_process),
            sources: Arc::clone(&sources),
        }),
    );
    let owner = service.create(true, CursorOptions::default()).unwrap();
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![item],
            open: true,
        }))
        .unwrap();
    while !matches!(
        owner.state().doc.workspace.resolutions.get(&id),
        Some(Resolution::Resolved { .. })
    ) {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    let live = service.item_ids();
    assert!(sources.look(&live).is_empty());
    fs::write(&overlay_file, "1,1,1,1,1,1\n").unwrap();
    assert!(
        sources.look(&live).is_empty(),
        "held back until it stops changing"
    );
    assert_eq!(sources.look(&live), [id]);
}
