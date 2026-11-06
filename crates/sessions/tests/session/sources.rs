//! A change to the files of an item: an open viewer keeps its stream and is told, and every session that holds the item
//! finds out.

use crate::{GRACE, Rig, authority, resolve, stream};
use foundation::CancelToken;
use handler_api::Settings;
use handlers::{HandlerSet, Handlers};
use playback::Authority;
use sessions::{
    CursorOptions,
    session::{Command, Completion, Handle, OnRuntime, Service, Work},
};
use std::{fs, path::Path, sync::Arc, time::Duration};
use workspace::{
    Content, ItemId, Watch, WorkspaceItem,
    intake::{NewOverlay, video},
};
use workspace_store::{DocCommand, Resolution};

/// A video with one overlay file in `dir`, and that file.
fn with_overlay(dir: &Path) -> (WorkspaceItem, std::path::PathBuf) {
    let (video_file, overlay_file) = (dir.join("a.mp4"), dir.join("gt.txt"));
    fs::write(&video_file, "x").unwrap();
    fs::write(&overlay_file, "").unwrap();
    let overlay = NewOverlay {
        path: overlay_file.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    (
        video(&video_file, &[overlay], &Handlers::built_in()).unwrap(),
        overlay_file,
    )
}

fn changed(owner: &Handle<u8>, item: ItemId) -> Option<u32> {
    owner.state().doc.workspace.changed.get(&item).copied()
}

#[tokio::test(start_paused = true)]
async fn an_open_viewer_keeps_its_stream_when_its_files_change_until_the_item_resolves_again() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    let (item, overlay) = with_overlay(rig.dir.path());
    let id = item.id;
    let handlers = Handlers::built_in();
    let (_, watch) = Watch::read(&item.recipe, &handlers, &CancelToken::new(), || {
        Err::<Content, ()>(())
    });
    let (_, key) = rig.open(&owner, item);
    rig.service
        .record_sources(owner.session(), id, watch.unwrap());
    let lease = stream(&rig.service, &owner, &key).unwrap();

    fs::write(&overlay, "1,1,1,1,1,1\n").unwrap();
    rig.service.check_sources();
    assert_eq!(
        changed(&owner, id),
        None,
        "held back until it looks the same twice"
    );
    rig.service.check_sources();
    assert_eq!(changed(&owner, id), Some(1));
    assert!(
        rig.jobs.take().is_empty(),
        "nothing resolves again by itself"
    );
    assert!(matches!(authority(&lease), Authority::Own { .. }));

    owner
        .command(Command::Content(DocCommand::Resolve(id)))
        .unwrap();
    assert_eq!(authority(&lease), Authority::Revoked);
    assert_eq!(changed(&owner, id), None);
}

#[tokio::test(start_paused = true)]
async fn what_a_superseded_job_read_is_never_recorded_for_the_item() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    let (item, overlay) = with_overlay(rig.dir.path());
    let id = item.id;
    let add = |item: &WorkspaceItem| {
        owner
            .command(Command::Content(DocCommand::AddItems {
                items: vec![item.clone()],
                open: true,
            }))
            .unwrap();
    };
    add(&item);
    let mut stale = rig.jobs.take();
    // The item is added again, which starts a new attempt and supersedes the first job; the new job finishes first.
    add(&item);
    rig.jobs.finish_all();

    // The superseded job read the files and completes late: its result is dropped, and so is what it read.
    let (spawn, done) = stale.remove(0);
    let (_, watch) = Watch::read(
        &item.recipe,
        &Handlers::built_in(),
        &CancelToken::new(),
        || Err::<Content, ()>(()),
    );
    done.watcher().record(id, watch.unwrap());
    let Work::Resolve(job) = &spawn.work;
    done.complete(Completion::Resolved(resolve(job)));

    fs::write(&overlay, "1,1,1,1,1,1\n").unwrap();
    rig.service.check_sources();
    rig.service.check_sources();
    assert_eq!(changed(&owner, id), None, "nothing is watched for the item");
}

#[tokio::test]
async fn resolving_records_the_files_and_each_window_with_the_item_is_told() {
    let dir = tempfile::tempdir().unwrap();
    let (item, overlay_file) = with_overlay(dir.path());
    let id = item.id;
    let service: Service<u8> = Service::new(
        GRACE,
        Arc::new(OnRuntime {
            runtime: tokio::runtime::Handle::current(),
            handlers: HandlerSet::new(Handlers::built_in()),
            environment: Arc::new(workspace::Environment::of_process),
        }),
    );
    // The same saved workspace open in two windows: one item id in two sessions.
    let owners: Vec<Handle<u8>> = (0..2)
        .map(|_| {
            let owner = service.create(true, CursorOptions::default()).unwrap();
            owner
                .command(Command::Content(DocCommand::AddItems {
                    items: vec![item.clone()],
                    open: true,
                }))
                .unwrap();
            owner
        })
        .collect();
    for owner in &owners {
        while !matches!(
            owner.state().doc.workspace.resolutions.get(&id),
            Some(Resolution::Resolved { .. })
        ) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }

    service.check_sources();
    fs::write(&overlay_file, "1,1,1,1,1,1\n").unwrap();
    service.check_sources();
    service.check_sources();
    for owner in &owners {
        assert_eq!(changed(owner, id), Some(1));
    }
}
