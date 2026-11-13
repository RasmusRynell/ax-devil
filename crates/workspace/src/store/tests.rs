//! The transitions of a workspace state.

use super::*;
use crate::Environment;
use foundation::{CancelToken, ErrorCode, Interrupt};
use handlers::Handlers;
use std::path::Path;

pub(super) fn item(path: &Path) -> WorkspaceItem {
    crate::intake::video(path, &[], &Handlers::built_in()).unwrap()
}

pub(super) fn add(
    state: &WorkspaceState,
    items: Vec<WorkspaceItem>,
    open: bool,
) -> (WorkspaceState, Vec<ResolveJob>) {
    apply(state, DocCommand::AddItems { items, open })
}

/// Resolves the job as a session would and commits it.
pub(super) fn finish(state: &WorkspaceState, job: &ResolveJob) -> WorkspaceState {
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

fn content_id(state: &WorkspaceState, id: ItemId) -> crate::ContentId {
    match &state.resolutions[&id] {
        Resolution::Resolved { content, .. } => content.id().clone(),
        other => panic!("expected a resolved item, got {other:?}"),
    }
}

pub(super) fn generation(state: &WorkspaceState, id: ItemId) -> ContentGen {
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
fn a_lane_matching_lands_in_the_recipe_by_slot_and_resolves_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let (video, overlay) = (folder.path().join("gate.mp4"), folder.path().join("gt.txt"));
    std::fs::write(&video, "x").unwrap();
    std::fs::write(&overlay, "x").unwrap();
    let overlays = [crate::intake::NewOverlay {
        path: overlay,
        handler: None,
        label: None,
        options: [("width", 64), ("height", 48)]
            .map(|(key, size)| (key.to_owned(), size.into()))
            .into_iter()
            .collect(),
    }];
    let added = crate::intake::video(&video, &overlays, &Handlers::built_in()).unwrap();
    let id = added.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], false);
    let state = finish(&state, &jobs[0]);
    let matching = scene::matching::MatchPolicy {
        basis: Some(scene::MatchBasis::Time),
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
    let new = crate::intake::NewLive {
        host: Some("203.0.113.5".into()),
        ..Default::default()
    };
    let defaults = crate::intake::LiveDefaults {
        host: String::new(),
        username: String::new(),
        password: crate::Secret::new(""),
        protocol: crate::Protocol::Http,
        head: 1,
        rtsp_port: 554,
        rtsp_route: crate::RtspRoute::Direct,
        resolution: String::new(),
        overlay: crate::intake::LiveMode::None,
        rtsp_handler: String::new(),
        mqtt: crate::intake::MqttDefaults {
            handler: String::new(),
            host: String::new(),
            user: String::new(),
            password: crate::Secret::new(""),
            port: 1883,
            source_key: String::new(),
        },
        datahub: crate::intake::DataHubDefaults {
            handler: String::new(),
            topic: String::new(),
            channel: 1,
        },
    };
    let added = crate::intake::live(new, &defaults, &Handlers::built_in()).unwrap();
    let id = added.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![added], false);
    let state = finish(&state, &jobs[0]);
    let matching = scene::matching::MatchPolicy {
        basis: Some(scene::MatchBasis::Time),
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
        set(crate::LIVE_SLOT).workspace.items[0]
            .recipe
            .lane_matching
            .get(crate::LIVE_SLOT),
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

    let rename = |state: &WorkspaceState, entered: &str| {
        let (next, jobs) = apply(
            state,
            DocCommand::RenameWorkspace {
                name: entered.into(),
                epoch: state.epoch,
            },
        );
        assert!(jobs.is_empty());
        assert_eq!(next.file, state.file, "the file keeps its name");
        next
    };
    let named = rename(&saved_now, "  Review  ");
    assert_eq!(named.workspace.name.as_deref(), Some("Review"));
    assert!(named.modified());
    assert!(
        !rename(&named, "Untitled").modified(),
        "Untitled is no name"
    );
    assert!(!rename(&named, "   ").modified(), "so is a blank one");
    let (stale, jobs) = apply(
        &named,
        DocCommand::RenameWorkspace {
            name: "Old".into(),
            epoch: named.epoch + 1,
        },
    );
    assert!(
        jobs.is_empty() && stale == named,
        "another epoch's name is ignored"
    );

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
            target: HiddenRef::Entry(crate::ContentId::of_item(&added.id)),
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
fn a_change_to_the_files_of_an_open_item_is_counted_and_any_other_resolves_again() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let (shown, listed) = (item(&file), item(&file));
    let (shown_id, listed_id) = (shown.id, listed.id);
    let (state, jobs) = add(&WorkspaceState::default(), vec![shown], true);
    let (state, more) = add(&state, vec![listed], false);
    let state = finish(&finish(&state, &jobs[0]), &more[0]);
    let stream = generation(&state, shown_id);

    // The viewer keeps its Content: resolving again would end its stream. It is told, once per change.
    let (counted, jobs) = apply(
        &state,
        DocCommand::SourceChanged(shown_id, Vec::new(), true),
    );
    let (counted, again) = apply(
        &counted,
        DocCommand::SourceChanged(shown_id, Vec::new(), true),
    );
    assert!(jobs.is_empty() && again.is_empty());
    assert_eq!(counted.changed[&shown_id].revision, 2);
    assert_eq!(generation(&counted, shown_id), stream);

    // An item nobody shows resolves again, and so does the open one when asked to: the count starts over.
    let (resolving, jobs) = apply(
        &counted,
        DocCommand::SourceChanged(listed_id, Vec::new(), true),
    );
    assert_eq!(jobs.len(), 1);
    let (reloading, jobs) = apply(&counted, DocCommand::Resolve(shown_id));
    assert_eq!(jobs.len(), 1);
    assert!(reloading.changed.len() == 1 && reloading.open.contains(&shown_id));
    let reloaded = finish(&reloading, &jobs[0]);
    assert!(generation(&reloaded, shown_id) > stream && reloaded.changed.is_empty());

    // A change while an item resolves restarts it: that attempt may have read the old files.
    let (restarted, jobs) = apply(
        &resolving,
        DocCommand::SourceChanged(listed_id, Vec::new(), true),
    );
    assert_eq!(jobs.len(), 1);
    assert_eq!(
        finish(&restarted, &jobs_of(&resolving, listed_id)),
        restarted
    );

    let (gone, _) = apply(&counted, DocCommand::Remove(shown_id));
    assert!(gone.changed.is_empty());
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
        label: item.label.clone(),
        recipe: item.recipe.clone(),
    }
}

mod edit;
mod hidden;
mod marks;
mod reload;
mod remove;
