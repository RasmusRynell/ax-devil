//! The transitions of a workspace state.

use super::*;
use foundation::ErrorCode;
use std::path::Path;

fn item(path: &Path) -> WorkspaceItem {
    workspace::intake::video(path, &[], &Handlers::built_in()).unwrap()
}

fn add(
    state: &WorkspaceState,
    items: Vec<WorkspaceItem>,
    open: bool,
) -> (WorkspaceState, Vec<ResolveJob>) {
    apply(state, DocCommand::AddItems { items, open })
}

/// Resolves the job as a session would and commits it.
fn finish(state: &WorkspaceState, job: &ResolveJob) -> WorkspaceState {
    let result = match resolve(
        job,
        &Handlers::built_in(),
        &Environment::of_process(),
        &CancelToken::new(),
    ) {
        Ok(content) => Ok(content),
        Err(Interrupt::Failed(error)) => Err(error),
        Err(Interrupt::Cancelled) => panic!("nothing cancels the job"),
    };
    complete(state, job.item, job.attempt, result)
}

fn content_id(state: &WorkspaceState, id: ItemId) -> workspace::ContentId {
    match &state.resolutions[&id] {
        Resolution::Resolved { content, .. } => content.id().clone(),
        other => panic!("expected a resolved item, got {other:?}"),
    }
}

fn generation(state: &WorkspaceState, id: ItemId) -> ContentGen {
    match &state.resolutions[&id] {
        Resolution::Resolved { generation, .. } => *generation,
        other => panic!("expected a resolved item, got {other:?}"),
    }
}

#[test]
fn items_are_listed_in_the_order_added_and_one_that_cannot_resolve_stays_with_its_reason() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let (opened, listed, broken) = (
        item(&file),
        item(&file),
        item(&folder.path().join("missing.mp4")),
    );
    let (opened_id, listed_id, broken_id) = (opened.id, listed.id, broken.id);

    let (state, first) = add(&WorkspaceState::default(), vec![opened], true);
    let (state, second) = add(&state, vec![listed, broken], false);
    let state = first
        .iter()
        .chain(&second)
        .fold(state, |state, job| finish(&state, job));

    let ids: Vec<ItemId> = state.workspace.items.iter().map(|item| item.id).collect();
    assert_eq!(ids, [opened_id, listed_id, broken_id]);
    let Resolution::Failed(error) = &state.resolutions[&broken_id] else {
        panic!(
            "expected a failure, got {:?}",
            state.resolutions[&broken_id]
        );
    };
    assert_eq!(error.code, ErrorCode::FileNotFound);
    assert_eq!(
        state.open,
        HashSet::from([opened_id]),
        "only adding with open requests a viewer"
    );
}

#[test]
fn an_already_resolved_item_is_added_ready_and_open_with_no_job() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let added = item(&file);
    let id = added.id;
    let (pending, jobs) = add(&WorkspaceState::default(), vec![added.clone()], false);
    let resolved = finish(&pending, &jobs[0]);
    let Resolution::Resolved { content, .. } = &resolved.resolutions[&id] else {
        panic!("the video resolves");
    };

    let (state, jobs) = apply(
        &WorkspaceState::default(),
        DocCommand::AddResolved {
            item: Box::new(added),
            content: Box::new(content.clone()),
            open: true,
        },
    );

    assert!(jobs.is_empty());
    assert_eq!(state.workspace.items.len(), 1);
    assert!(state.open.contains(&id));
    assert_eq!(content_id(&state, id), *content.id());
}

#[test]
fn a_result_is_dropped_unless_its_item_still_waits_on_that_attempt() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let added = item(&file);
    let id = added.id;
    let (state, first) = add(&WorkspaceState::default(), vec![added.clone()], false);
    let (state, second) = add(&state, vec![added], false);

    assert_eq!(
        state.workspace.items.len(),
        1,
        "adding an item again replaces it"
    );
    assert_ne!(
        first[0].attempt, second[0].attempt,
        "and resolves it under a new attempt"
    );
    let stale = finish(&state, &first[0]);
    assert_eq!(
        stale, state,
        "the replaced attempt's result changes nothing"
    );
    let current = finish(&stale, &second[0]);
    assert_eq!(generation(&current, id), ContentGen(1));
    assert_eq!(
        finish(&current, &second[0]),
        current,
        "a result commits once"
    );
}

#[test]
fn closing_and_reopening_a_viewer_resolves_again_under_a_new_generation_with_the_same_content_id() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let added = item(&file);
    let id = added.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], true);
    let opened = finish(&state, &jobs[0]);
    let first = generation(&opened, id);
    let before = content_id(&opened, id);

    let (closed, jobs) = apply(&opened, DocCommand::CloseViewer(id));
    assert!(jobs.is_empty() && !closed.open.contains(&id));
    assert_eq!(generation(&closed, id), first, "closing keeps the Content");
    let (reopened, jobs) = apply(&closed, DocCommand::OpenViewer(id));
    assert!(reopened.open.contains(&id));
    let reopened = finish(&reopened, &jobs[0]);

    assert!(generation(&reopened, id) > first);
    assert_eq!(content_id(&reopened, id), before);
    let (again, jobs) = apply(&reopened, DocCommand::OpenViewer(id));
    assert!(
        jobs.is_empty() && again == reopened,
        "an open viewer stays as it is"
    );

    let (resolving, jobs) = apply(&reopened, DocCommand::Resolve(id));
    assert!(
        resolving.open.contains(&id) && jobs.len() == 1,
        "Resolve works on an open viewer"
    );
    let (waiting, jobs) = apply(&resolving, DocCommand::Resolve(id));
    assert!(
        jobs.is_empty() && waiting == resolving,
        "and waits for the attempt in flight"
    );
}

#[test]
fn hiding_twice_changes_nothing_and_a_reference_to_nothing_is_kept_but_hides_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let added = item(&file);
    let entry = ContentId::of_item(&added.id);
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], false);
    let state = finish(&state, &jobs[0]);
    let set = |state: &WorkspaceState, target: &HiddenRef, hidden: bool| {
        let (next, jobs) = apply(
            state,
            DocCommand::SetHidden {
                target: target.clone(),
                hidden,
            },
        );
        assert!(jobs.is_empty(), "hiding resolves nothing");
        next
    };
    let target = HiddenRef::Entry(entry.clone());
    let orphan = HiddenRef::Lane(entry, "gone".into());

    let hidden = set(&state, &target, true);
    assert!(hidden.is_hidden(&target));
    assert_eq!(
        set(&hidden, &target, true),
        hidden,
        "hiding again changes nothing"
    );
    let with_orphan = set(&hidden, &orphan, true);
    assert_eq!(
        rows(&with_orphan).iter().filter(|row| row.hidden).count(),
        1
    );
    let shown = set(&set(&with_orphan, &target, false), &orphan, false);
    assert_eq!(shown, state, "showing undoes hiding");
}

#[test]
fn a_lane_matching_lands_in_the_recipe_by_slot_and_resolves_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let (video, overlay) = (folder.path().join("gate.mp4"), folder.path().join("gt.txt"));
    std::fs::write(&video, "x").unwrap();
    std::fs::write(&overlay, "x").unwrap();
    let overlays = [workspace::intake::NewOverlay {
        path: overlay,
        handler: None,
        label: None,
        options: [("width", 64), ("height", 48)]
            .map(|(key, size)| (key.to_owned(), size.into()))
            .into_iter()
            .collect(),
    }];
    let added = workspace::intake::video(&video, &overlays, &Handlers::built_in()).unwrap();
    let id = added.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], false);
    let state = finish(&state, &jobs[0]);
    let matching = overlay_sync::MatchPolicy {
        basis: Some(overlay_sync::MatchBasis::Time),
        ..Default::default()
    };
    let set = |state: &WorkspaceState, item, slot: &str| {
        let (next, jobs) = apply(
            state,
            DocCommand::SetLaneMatching {
                item,
                slot: slot.to_owned(),
                matching,
            },
        );
        assert!(jobs.is_empty(), "the Content does not depend on matching");
        next
    };

    let state = set(&state, id, "gt");
    assert_eq!(
        state.workspace.items[0].recipe.lane_matching.get("gt"),
        Some(&matching)
    );
    for (item, slot) in [(id, "nope"), (ItemId::generate(), "gt")] {
        assert_eq!(
            set(&state, item, slot),
            state,
            "a slot or item the workspace lacks changes nothing"
        );
    }
    let (resolving, _) = apply(&state, DocCommand::OpenViewer(id));
    assert!(
        set(&resolving, id, "later").workspace.items[0]
            .recipe
            .lane_matching
            .contains_key("later"),
        "an item that is resolving again keeps the choice"
    );
}

#[test]
fn a_live_streams_matching_is_kept_under_its_one_slot() {
    let new = workspace::intake::NewLive {
        host: Some("203.0.113.5".into()),
        ..Default::default()
    };
    let defaults = workspace::intake::LiveDefaults {
        host: String::new(),
        username: String::new(),
        password: workspace::Secret::new(""),
        protocol: workspace::Protocol::Http,
        head: 1,
        rtsp_port: 554,
        resolution: String::new(),
        overlay: workspace::intake::LiveMode::None,
        rtsp_handler: String::new(),
        mqtt: workspace::intake::MqttDefaults {
            handler: String::new(),
            host: String::new(),
            user: String::new(),
            password: workspace::Secret::new(""),
            port: 1883,
            source_key: String::new(),
        },
        datahub: workspace::intake::DataHubDefaults {
            handler: String::new(),
            topic: String::new(),
            channel: 1,
        },
    };
    let added = workspace::intake::live(new, &defaults, &Handlers::built_in()).unwrap();
    let id = added.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], false);
    let state = finish(&state, &jobs[0]);
    let matching = overlay_sync::MatchPolicy {
        basis: Some(overlay_sync::MatchBasis::Time),
        ..Default::default()
    };
    let set = |slot: &str| {
        apply(
            &state,
            DocCommand::SetLaneMatching {
                item: id,
                slot: slot.to_owned(),
                matching,
            },
        )
        .0
    };
    assert_eq!(
        set(workspace::LIVE_SLOT).workspace.items[0]
            .recipe
            .lane_matching
            .get(workspace::LIVE_SLOT),
        Some(&matching)
    );
    assert_eq!(
        set("nope"),
        state,
        "a slot the stream lacks changes nothing"
    );
}

fn replace(workspace: Workspace, saved: Saved) -> DocCommand {
    DocCommand::Replace {
        workspace,
        file: None,
        saved,
    }
}

#[test]
fn edits_are_modified_until_reverted_or_saved_and_a_recovered_workspace_is_modified_until_saved() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let original = item(&file);
    let saved_as = Workspace {
        items: vec![original.clone()],
        ..Default::default()
    };
    let (state, _) = apply(
        &WorkspaceState::default(),
        replace(saved_as.clone(), Saved::As(saved_as.clone())),
    );
    assert!(!state.modified());

    let mut renamed = original.clone();
    renamed.label = "renamed".into();
    let (edited, _) = add(&state, vec![renamed], false);
    assert!(edited.modified());
    let (reverted, _) = add(&edited, vec![original], false);
    assert!(!reverted.modified(), "putting the value back is unmodified");

    let (recovered, _) = apply(
        &state,
        replace(saved_as.clone(), Saved::Recovered { offer: true }),
    );
    assert!(
        recovered.modified(),
        "a restored copy is modified though equal"
    );
    let (kept, _) = apply(&recovered, DocCommand::Keep);
    assert_eq!(kept.saved, Saved::Recovered { offer: false });
    assert!(kept.modified(), "keeping is not saving");
    assert_eq!(apply(&state, DocCommand::Keep).0, state);

    let file = HostPath::new(folder.path().join("ws.ax-devil.workspace"));
    let saved = |workspace: &Workspace| DocCommand::MarkSaved {
        workspace: workspace.clone(),
        file: file.clone(),
    };
    let (saved_now, _) = apply(&recovered, saved(&saved_as));
    assert!(!saved_now.modified());
    assert_eq!(saved_now.file.as_ref(), Some(&file));

    let (later, _) = add(
        &recovered,
        vec![item(&folder.path().join("other.mp4"))],
        false,
    );
    let (partly, _) = apply(&later, saved(&saved_as));
    assert!(
        partly.modified(),
        "edits made after the snapshot stay modified"
    );
}

#[test]
fn replacing_the_workspace_closes_viewers_bumps_the_epoch_and_never_reuses_a_generation() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let added = item(&file);
    let (state, jobs) = add(&WorkspaceState::default(), vec![added.clone()], true);
    let state = finish(&state, &jobs[0]);
    let (state, _) = apply(
        &state,
        DocCommand::SetHidden {
            target: HiddenRef::Entry(ContentId::of_item(&added.id)),
            hidden: true,
        },
    );
    let before = generation(&state, added.id);

    let incoming = Workspace {
        items: vec![added.clone()],
        ..Default::default()
    };
    let (replaced, jobs) = apply(&state, replace(incoming.clone(), Saved::As(incoming)));
    assert_eq!(replaced.epoch, state.epoch + 1);
    assert!(replaced.open.is_empty() && replaced.hidden.is_empty());
    assert_eq!(jobs.len(), 1, "every item resolves again");
    assert!(matches!(
        replaced.resolutions[&added.id],
        Resolution::Pending { .. }
    ));
    let resolved = finish(&replaced, &jobs[0]);
    assert!(generation(&resolved, added.id) > before);
}

#[test]
fn removing_an_item_takes_its_viewer_and_resolution_with_it_and_drops_a_result_still_on_its_way() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let (kept, removed) = (item(&file), item(&file));
    let (kept_id, removed_id) = (kept.id, removed.id);
    let (state, jobs) = add(&WorkspaceState::default(), vec![kept, removed], true);
    let resolved = finish(&state, &jobs[0]);

    let (gone, jobs) = apply(&resolved, DocCommand::Remove(removed_id));

    assert!(jobs.is_empty());
    let ids: Vec<_> = gone.workspace.items.iter().map(|item| item.id).collect();
    assert_eq!(ids, [kept_id]);
    assert!(gone.open.contains(&kept_id) && !gone.open.contains(&removed_id));
    assert!(gone.resolutions.contains_key(&kept_id) && !gone.resolutions.contains_key(&removed_id));
    assert_eq!(
        finish(&gone, &jobs_of(&state, removed_id)),
        gone,
        "the result of the removed item's attempt is dropped"
    );
    let (again, _) = apply(&gone, DocCommand::Remove(removed_id));
    assert_eq!(again, gone, "removing it twice changes nothing");
}

/// The job that was resolving `id` in `state`.
fn jobs_of(state: &WorkspaceState, id: ItemId) -> ResolveJob {
    let Some(Resolution::Pending { attempt }) = state.resolutions.get(&id) else {
        panic!("the item waits on an attempt");
    };
    let item = state
        .workspace
        .items
        .iter()
        .find(|item| item.id == id)
        .unwrap();
    ResolveJob {
        item: id,
        attempt: *attempt,
        name: item.display_name(),
        recipe: item.recipe.clone(),
    }
}
