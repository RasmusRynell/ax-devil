//! Reload validation, failures and media ownership.

use crate::{GRACE, authority, entry_key, playlist_in, showing, stream};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handler_api::{PlaylistResolver, ResolvedPlaylists, ResolverSpec, Values};
use handlers::{HandlerSet, Handlers};
use sessions::{
    CursorOptions,
    session::{Access, Axis, Command, Control, Layout, OnRuntime, Service, ViewerId},
};
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
use workspace::store::{DocCommand, Reload, Resolution};
use workspace::{Source, VideoRef};

/// A resolver with the folder-pair form whose run panics, as a defective plugin's would.
struct Panics(ResolverSpec);

impl PlaylistResolver for Panics {
    fn spec(&self) -> &ResolverSpec {
        &self.0
    }

    fn resolve(
        &self,
        _settings: &Values,
        _cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        panic!("the resolver has a defect")
    }
}

#[tokio::test]
async fn a_resolver_that_panics_fails_its_item_instead_of_leaving_it_resolving() {
    let dir = tempfile::tempdir().unwrap();
    let mut item = playlist_in(dir.path(), &["a"]);
    let mut spec = Handlers::built_in()
        .resolver("folder_pair")
        .unwrap()
        .spec()
        .clone();
    spec.id = "panics".into();
    let Source::Playlist(recipe) = &mut item.recipe.source else {
        unreachable!("playlist_in makes a playlist")
    };
    recipe.resolver = "panics".into();
    let id = item.id;
    let handlers = Handlers::built_in().with_resolvers([Arc::new(Panics(spec)) as Arc<_>]);
    let service: Service<u8> = Service::new(
        GRACE,
        Arc::new(OnRuntime {
            runtime: tokio::runtime::Handle::current(),
            handlers: HandlerSet::new(handlers),
            environment: Arc::new(workspace::Environment::of_process),
            opens: Arc::new(|_, _| Ok(())),
        }),
    );
    let owner = service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![item],
            open: false,
        }))
        .unwrap();
    let mut states = service.subscribe(owner.session()).unwrap();
    let failed = tokio::time::timeout(
        Duration::from_secs(10),
        states.wait_for(|state| {
            matches!(
                state.doc.workspace.resolutions.get(&id),
                Some(Resolution::Failed(_))
            )
        }),
    )
    .await
    .expect("a resolver that panicked left its item resolving instead of failed")
    .unwrap();
    let Some(Resolution::Failed(failure)) = failed.doc.workspace.resolutions.get(&id) else {
        unreachable!("wait_for observed a failed resolution")
    };
    assert!(
        failure.reason.contains("Resolving stopped unexpectedly"),
        "{}",
        failure.reason
    );
}

#[tokio::test]
async fn a_reload_checks_the_shown_entry_and_keeps_its_generation_on_open_failure() {
    let dir = tempfile::tempdir().unwrap();
    let item = playlist_in(dir.path(), &["a", "b"]);
    let id = item.id;
    let checked = Arc::new(AtomicUsize::new(0));
    let calls = Arc::clone(&checked);
    let service: Service<u8> = Service::new(
        GRACE,
        Arc::new(OnRuntime {
            runtime: tokio::runtime::Handle::current(),
            handlers: HandlerSet::new(Handlers::built_in()),
            environment: Arc::new(workspace::Environment::of_process),
            opens: Arc::new(move |video, _| {
                calls.fetch_add(1, Ordering::SeqCst);
                if matches!(video, VideoRef::File(path) if path.file_stem().unwrap() == "b") {
                    Err(Interrupt::Failed(UserError::new(
                        ErrorCode::FileUnreadable,
                        "B cannot open",
                    )))
                } else {
                    Ok(())
                }
            }),
        }),
    );
    let owner = service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    let mut states = service.subscribe(owner.session()).unwrap();
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![item],
            open: true,
        }))
        .unwrap();
    tokio::time::timeout(
        Duration::from_secs(10),
        states.wait_for(|state| {
            matches!(
                state.doc.workspace.resolutions.get(&id),
                Some(Resolution::Resolved { .. })
            )
        }),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(
        checked.load(Ordering::SeqCst),
        0,
        "first resolution must not open media"
    );
    let a = entry_key(&owner.state(), id, 0);
    let b = entry_key(&owner.state(), id, 1);
    let mut view = showing(id);
    view.viewers.get_mut(&b.viewer).unwrap().entry = Some(b.entry.clone());
    owner.view(view.clone(), 1).unwrap();
    let old = stream(&service, &owner, &b).unwrap();
    owner
        .command(Command::Content(DocCommand::Resolve(id)))
        .unwrap();
    tokio::time::timeout(
        Duration::from_secs(10),
        states.wait_for(|state| {
            !state.doc.workspace.reloads.contains_key(&id)
                || matches!(
                    state.doc.workspace.reloads.get(&id),
                    Some(Reload::Failed { .. })
                )
        }),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(entry_key(&owner.state(), id, 1).generation, b.generation);
    let state = owner.state();
    let Some(Reload::Failed { error, .. }) = state.doc.workspace.reloads.get(&id) else {
        panic!("replacement opening must report a reload failure");
    };
    assert_eq!(error.reason, "B cannot open");
    assert!(matches!(authority(&old), playback::Authority::Own { .. }));
    view.viewers.get_mut(&a.viewer).unwrap().entry = Some(a.entry.clone());
    owner.view(view, 2).unwrap();
    owner
        .command(Command::Content(DocCommand::Resolve(id)))
        .unwrap();
    tokio::time::timeout(
        Duration::from_secs(10),
        states.wait_for(|state| {
            !matches!(
                state.doc.workspace.reloads.get(&id),
                Some(Reload::Running { .. })
            )
        }),
    )
    .await
    .unwrap()
    .unwrap();
    assert!(entry_key(&owner.state(), id, 0).generation > a.generation);
    assert!(!owner.state().doc.workspace.reloads.contains_key(&id));
    assert_eq!(checked.load(Ordering::SeqCst), 2);
}

// Acknowledged blocking gate; dropping release also frees the worker.
fn hold() -> (
    impl FnOnce() + Send + 'static,
    tokio::sync::oneshot::Receiver<()>,
    std::sync::mpsc::Sender<()>,
) {
    let (begun, began) = tokio::sync::oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let wait = move || {
        let _ = begun.send(());
        match released.recv_timeout(Duration::from_secs(10)) {
            Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => panic!("check was not released"),
        }
    };
    (wait, began, release)
}

/// Switches overlay references after resolving real folder pairs.
struct MovingOverlays {
    spec: ResolverSpec,
    inner: Arc<dyn PlaylistResolver>,
    replacement: Arc<AtomicBool>,
    files: [std::path::PathBuf; 2],
}

impl PlaylistResolver for MovingOverlays {
    fn spec(&self) -> &ResolverSpec {
        &self.spec
    }

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        let mut content = self.inner.resolve(settings, cancel)?;
        let file = &self.files[usize::from(self.replacement.load(Ordering::SeqCst))];
        for playlist in &mut content.playlists {
            for entry in &mut playlist.entries {
                entry.overlays[0].path = file.clone();
            }
        }
        Ok(content)
    }
}

#[tokio::test]
async fn a_reload_validates_entries_shown_meanwhile_before_it_commits() {
    tokio::time::timeout(Duration::from_secs(20), async {
        for (exploring, broken, removed) in [
            (false, false, false), (false, true, false), (true, false, false), (true, true, false),
            (false, false, true), (false, true, true),
        ] {
                let dir = tempfile::tempdir().unwrap();
                let mut item = playlist_in(dir.path(), if removed { &["a", "b"] } else { &["a", "b", "c"] });
                let id = item.id;
                let files = [dir.path().join("old.txt"), dir.path().join("new.txt")];
                for file in &files { std::fs::write(file, "").unwrap(); }
                let replacement = Arc::new(AtomicBool::new(false));
                let base = Handlers::built_in();
                let mut spec = base.resolver("folder_pair").unwrap().spec().clone();
                spec.id = "moving-overlays".into();
                let Source::Playlist(recipe) = &mut item.recipe.source else { unreachable!(); };
                recipe.resolver = spec.id.clone();
                let resolver = MovingOverlays { spec, inner: Arc::clone(base.resolver("folder_pair").unwrap()), replacement: Arc::clone(&replacement), files: files.clone() };
                let handlers = base.with_resolvers([Arc::new(resolver) as Arc<dyn PlaylistResolver>]);
                let (wait_a, began_a, release_a) = hold();
                let (wait_b, began_b, release_b) = hold();
                let (wait_c, began_c, release_c) = hold();
                let gates = Arc::new(Mutex::new(std::collections::HashMap::from([
                    ("a", wait_a), ("b", wait_b), ("c", wait_c),
                ])));
                let calls = Arc::new(Mutex::new(Vec::new()));
                let opened = Arc::clone(&calls);
                let service: Service<u8> = Service::new(GRACE, Arc::new(OnRuntime {
                    runtime: tokio::runtime::Handle::current(),
                    handlers: HandlerSet::new(handlers),
                    environment: Arc::new(workspace::Environment::of_process),
                    opens: Arc::new(move |video, _| {
                        let VideoRef::File(path) = video else { panic!("fixture video is a file"); };
                        let name = path.file_stem().unwrap().to_str().unwrap();
                        opened.lock().unwrap().push(name.to_owned());
                        let wait = gates.lock().unwrap().remove(name);
                        if let Some(wait) = wait { wait(); }
                        if broken && name == if removed { "a" } else { "b" } {
                            return Err(Interrupt::Failed(UserError::new(ErrorCode::FileUnreadable, format!("{} cannot open", name.to_uppercase()))));
                        }
                        Ok(())
                    }),
                }));
                let owner = service.create(true, CursorOptions::default(), Default::default()).unwrap();
                let mut states = service.subscribe(owner.session()).unwrap();
                owner.command(Command::Content(DocCommand::AddItems { items: vec![item], open: true })).unwrap();
                states.wait_for(|state| matches!(state.doc.workspace.resolutions.get(&id), Some(Resolution::Resolved { .. }))).await.unwrap();
                let keys: Vec<_> = (0..if removed { 2 } else { 3 }).map(|index| entry_key(&owner.state(), id, index)).collect();
                let mut view = showing(id);
                view.viewers.get_mut(&keys[0].viewer).unwrap().entry = Some(keys[0].entry.clone());
                if removed {
                    // B disappears, while a second presenter pane must keep showing A.
                    view.viewers.get_mut(&keys[1].viewer).unwrap().entry = Some(keys[1].entry.clone());
                    let mut a = view.viewers[&keys[0].viewer].clone();
                    a.entry = Some(keys[0].entry.clone());
                    view.viewers.insert(ViewerId(2), a);
                    view.layout = Some(Layout::Split {
                        axis: Axis::Row, share: 5000,
                        first: crate::pane(1), second: crate::pane(2),
                    });
                }
                owner.view(view.clone(), 1).unwrap();
                let guest = if exploring {
                    owner.command(Command::Control(Control::Share { access: Access::Explore })).unwrap();
                    let guest = service.join(&crate::link(&owner), CursorOptions::default()).unwrap();
                    guest.view(view.clone(), 1).unwrap();
                    Some(guest)
                } else { None };
                let navigating = guest.as_ref().unwrap_or(&owner);
                let old_pair = if removed {
                    let mut a = keys[0].clone();
                    a.viewer = ViewerId(2);
                    let streams = (stream(&service, &owner, &a).unwrap(), stream(&service, &owner, &keys[1]).unwrap());
                    std::fs::remove_file(dir.path().join("overlays/b.txt")).unwrap();
                    Some(streams)
                } else { None };
                replacement.store(true, Ordering::SeqCst);
                // Resolve without a source-change offer.
                owner.command(Command::Content(DocCommand::Resolve(id))).unwrap();
                tokio::select! {
                    began = began_a => began.unwrap(),
                    _ = states.wait_for(|state| !matches!(state.doc.workspace.reloads.get(&id), Some(Reload::Running { .. }))) => panic!("reload ended without validating surviving A"),
                }
                if removed {
                    release_a.send(()).unwrap();
                    states.wait_for(|state| !matches!(state.doc.workspace.reloads.get(&id), Some(Reload::Running { .. }))).await.unwrap();
                    let state = owner.state();
                    assert_eq!(&*calls.lock().unwrap(), &["a"], "only surviving A may be opened, exactly once");
                    if broken {
                        assert_eq!(entry_key(&state, id, 0).generation, keys[0].generation);
                        let Some(Reload::Failed { error, .. }) = state.doc.workspace.reloads.get(&id) else { panic!("A's failure must retain both old viewers"); };
                        assert_eq!(error.reason, "A cannot open");
                        assert_eq!(state.presenter.viewers.len(), 2);
                        let (a, b) = old_pair.as_ref().unwrap();
                        for lease in [a, b] { assert!(matches!(authority(lease), playback::Authority::Own { .. })); }
                    } else {
                        assert!(entry_key(&state, id, 0).generation > keys[0].generation);
                        assert!(!state.presenter.viewers.contains_key(&keys[1].viewer));
                        assert_eq!(state.presenter.viewers[&ViewerId(2)].entry, Some(keys[0].entry.clone()));
                        assert_eq!(state.presenter.viewers.len(), 1);
                        assert!(!state.doc.workspace.reloads.contains_key(&id));
                    }
                    continue;
                }
                view.viewers.get_mut(&keys[1].viewer).unwrap().entry = Some(keys[1].entry.clone());
                navigating.view(view.clone(), 2).unwrap();
                let old = stream(&service, navigating, &keys[1]).unwrap();
                let before = owner.state();
                states.borrow_and_update();
                release_a.send(()).unwrap();
                tokio::select! {
                    began = began_b => began.unwrap(),
                    _ = states.wait_for(|state| !matches!(state.doc.workspace.reloads.get(&id), Some(Reload::Running { .. }))) => panic!("reload completed before B was validated"),
                }
                assert_eq!(owner.state().revision, before.revision, "another validation round must not change the Session revision");
                assert!(!states.has_changed().unwrap(), "another validation round must not publish state");
                assert_eq!(entry_key(&owner.state(), id, 1).generation, keys[1].generation);
                if !broken {
                    // The active Content must keep its watch until commit.
                    std::fs::write(&files[0], "old changed").unwrap();
                    service.check_sources(); service.check_sources();
                    assert_eq!(owner.state().doc.workspace.changed.get(&id), Some(&1), "retry must not record the replacement watch");
                }
                view.viewers.get_mut(&keys[2].viewer).unwrap().entry = Some(keys[2].entry.clone());
                navigating.view(view.clone(), 3).unwrap();
                let before = owner.state();
                states.borrow_and_update();
                release_b.send(()).unwrap();
                if !broken {
                    tokio::select! {
                        began = began_c => began.unwrap(),
                        _ = states.wait_for(|state| !matches!(state.doc.workspace.reloads.get(&id), Some(Reload::Running { .. }))) => panic!("reload completed before C was validated"),
                    }
                    assert_eq!(owner.state().revision, before.revision, "cumulative validation must not publish another revision");
                    assert!(!states.has_changed().unwrap());
                    // Cycle through covered entries while C opens: none may be checked twice.
                    for (seq, index) in [(4, 1), (5, 0), (6, 2), (7, 1)] {
                        view.viewers.get_mut(&keys[index].viewer).unwrap().entry = Some(keys[index].entry.clone());
                        navigating.view(view.clone(), seq).unwrap();
                    }
                    release_c.send(()).unwrap();
                }
                states.wait_for(|state| !matches!(state.doc.workspace.reloads.get(&id), Some(Reload::Running { .. }))).await.unwrap();
                let state = owner.state();
                if broken {
                    assert_eq!(entry_key(&state, id, 1).generation, keys[1].generation);
                    let Some(Reload::Failed { error, .. }) = state.doc.workspace.reloads.get(&id) else { panic!("the failed round must report B's error, even after navigation to C"); };
                    assert_eq!(error.reason, "B cannot open");
                    assert!(matches!(authority(&old), playback::Authority::Own { .. }));
                    assert!(!state.doc.workspace.changed.contains_key(&id));
                } else {
                    assert!(entry_key(&state, id, 1).generation > keys[1].generation);
                    assert!(!state.doc.workspace.reloads.contains_key(&id));
                    // Commit replaces the watch: old paths retire, new paths become watched.
                    std::fs::write(&files[0], "old changed again").unwrap();
                    service.check_sources(); service.check_sources();
                    assert_eq!(owner.state().doc.workspace.changed.get(&id), Some(&1), "commit must retire the old overlay watch");
                    std::fs::write(&files[1], "new changed").unwrap();
                    service.check_sources(); service.check_sources();
                    assert_eq!(owner.state().doc.workspace.changed.get(&id), Some(&2), "commit must publish the retained watch");
                }
                let calls = calls.lock().unwrap();
                for name in if broken { &["a", "b"][..] } else { &["a", "b", "c"][..] } {
                    assert_eq!(calls.iter().filter(|call| call == name).count(), 1, "each entry may be validated only once");
                }
                assert_eq!(calls.len(), if broken { 2 } else { 3 });
        }
    }).await.expect("cumulative validation workflow did not finish");
}
