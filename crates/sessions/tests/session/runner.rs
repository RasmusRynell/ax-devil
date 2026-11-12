//! The real runner reports resolver and replacement-media failures without losing shown Content.

use crate::{GRACE, authority, entry_key, playlist_in, showing, stream};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handler_api::{PlaylistResolver, ResolvedPlaylists, ResolverSpec, Values};
use handlers::{HandlerSet, Handlers};
use sessions::{
    CursorOptions,
    session::{Command, OnRuntime, Service},
};
use std::{sync::Arc, time::Duration};
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
    let checked = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let calls = Arc::clone(&checked);
    let service: Service<u8> = Service::new(
        GRACE,
        Arc::new(OnRuntime {
            runtime: tokio::runtime::Handle::current(),
            handlers: HandlerSet::new(Handlers::built_in()),
            environment: Arc::new(workspace::Environment::of_process),
            opens: Arc::new(move |video, _| {
                calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
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
        checked.load(std::sync::atomic::Ordering::SeqCst),
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
    assert_eq!(checked.load(std::sync::atomic::Ordering::SeqCst), 2);
}

// The same acknowledged blocking gate as the source-open tests: dropping release frees the worker too.
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

#[tokio::test]
async fn navigation_outside_reload_validation_keeps_the_generation_and_offers_reload_again() {
    tokio::time::timeout(Duration::from_secs(20), async {
        for exploring in [false, true] {
            let dir = tempfile::tempdir().unwrap();
            let item = playlist_in(dir.path(), &["a", "b"]);
            let id = item.id;
            let (wait, began, release) = hold();
            let gate = Arc::new(std::sync::Mutex::new(Some(wait)));
            let checking = Arc::clone(&gate);
            let service: Service<u8> = Service::new(
                GRACE,
                Arc::new(OnRuntime {
                    runtime: tokio::runtime::Handle::current(),
                    handlers: HandlerSet::new(Handlers::built_in()),
                    environment: Arc::new(workspace::Environment::of_process),
                    opens: Arc::new(move |video, _| {
                        if matches!(video, VideoRef::File(path) if path.file_stem().unwrap() == "b")
                        {
                            return Err(Interrupt::Failed(UserError::new(
                                ErrorCode::FileUnreadable,
                                "B cannot open",
                            )));
                        }
                        let wait = checking.lock().unwrap().take();
                        if let Some(wait) = wait {
                            wait();
                        }
                        Ok(())
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
            states
                .wait_for(|state| {
                    matches!(
                        state.doc.workspace.resolutions.get(&id),
                        Some(Resolution::Resolved { .. })
                    )
                })
                .await
                .unwrap();
            let a = entry_key(&owner.state(), id, 0);
            let b = entry_key(&owner.state(), id, 1);
            let mut view = showing(id);
            view.viewers.get_mut(&a.viewer).unwrap().entry = Some(a.entry.clone());
            owner.view(view.clone(), 1).unwrap();
            let guest = if exploring {
                owner
                    .command(Command::Control(sessions::session::Control::Share {
                        access: sessions::session::Access::Explore,
                    }))
                    .unwrap();
                let guest = service
                    .join(&crate::link(&owner), CursorOptions::default())
                    .unwrap();
                guest.view(view.clone(), 1).unwrap();
                Some(guest)
            } else {
                None
            };
            let navigating = guest.as_ref().unwrap_or(&owner);
            owner
                .command(Command::Content(DocCommand::SourceChanged(id)))
                .unwrap();
            owner
                .command(Command::Content(DocCommand::Resolve(id)))
                .unwrap();
            began.await.unwrap();
            view.viewers.get_mut(&b.viewer).unwrap().entry = Some(b.entry.clone());
            navigating.view(view.clone(), 2).unwrap();
            let old = stream(&service, navigating, &b).unwrap();
            release.send(()).unwrap();
            states
                .wait_for(|state| {
                    !matches!(
                        state.doc.workspace.reloads.get(&id),
                        Some(Reload::Running { .. })
                    )
                })
                .await
                .unwrap();
            assert_eq!(entry_key(&owner.state(), id, 1).generation, b.generation);
            assert!(
                !owner.state().doc.workspace.reloads.contains_key(&id),
                "navigation discards without a failure"
            );
            assert_eq!(
                owner.state().doc.workspace.changed.get(&id),
                Some(&1),
                "the offer remains at the same generation and count"
            );
            assert!(matches!(authority(&old), playback::Authority::Own { .. }));

            owner
                .command(Command::Content(DocCommand::Resolve(id)))
                .unwrap();
            states
                .wait_for(|state| {
                    !matches!(
                        state.doc.workspace.reloads.get(&id),
                        Some(Reload::Running { .. })
                    )
                })
                .await
                .unwrap();
            let state = owner.state();
            let Some(Reload::Failed { error, .. }) = state.doc.workspace.reloads.get(&id) else {
                panic!("retry from B must report its open failure");
            };
            assert_eq!(error.reason, "B cannot open");
            assert_eq!(entry_key(&state, id, 1).generation, b.generation);

            // Navigating back into the validated set permits publication, even though the selection changed twice.
            view.viewers.get_mut(&a.viewer).unwrap().entry = Some(a.entry.clone());
            navigating.view(view.clone(), 3).unwrap();
            let (wait, began, release) = hold();
            *gate.lock().unwrap() = Some(wait);
            owner
                .command(Command::Content(DocCommand::Resolve(id)))
                .unwrap();
            began.await.unwrap();
            view.viewers.get_mut(&b.viewer).unwrap().entry = Some(b.entry.clone());
            navigating.view(view.clone(), 4).unwrap();
            view.viewers.get_mut(&a.viewer).unwrap().entry = Some(a.entry.clone());
            navigating.view(view, 5).unwrap();
            release.send(()).unwrap();
            states
                .wait_for(|state| {
                    !matches!(
                        state.doc.workspace.reloads.get(&id),
                        Some(Reload::Running { .. })
                    )
                })
                .await
                .unwrap();
            assert!(entry_key(&owner.state(), id, 0).generation > a.generation);
            assert!(!owner.state().doc.workspace.reloads.contains_key(&id));
            assert!(!owner.state().doc.workspace.changed.contains_key(&id));
        }
    })
    .await
    .expect("navigation reload workflow did not finish");
}
