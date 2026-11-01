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
    let result = match resolve(job, &Handlers::built_in(), &CancelToken::new()) {
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
