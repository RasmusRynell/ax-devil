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
