//! Items added together with a viewer requested are published together, in the order added.

use super::*;
use crate::Workspace;
use crate::store::tests::{add, finish, generation, item};

/// Three items, the first of which cannot resolve, added with a viewer requested: `(state, ids, jobs)`.
fn three(folder: &std::path::Path) -> (WorkspaceState, [ItemId; 3], Vec<ResolveJob>) {
    let file = folder.join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let items = vec![item(&folder.join("missing.mp4")), item(&file), item(&file)];
    let ids = [items[0].id, items[1].id, items[2].id];
    let (state, jobs) = add(&WorkspaceState::default(), items, true);
    (state, ids, jobs)
}

#[test]
fn results_are_held_until_the_whole_batch_has_one_then_published_in_order_and_only_the_first_that_resolved_opens()
 {
    let folder = tempfile::tempdir().unwrap();
    let (state, [failing, second, third], jobs) = three(folder.path());

    let state = finish(&state, &jobs[1]);
    let state = finish(&state, &jobs[0]);
    assert!(
        state
            .resolutions
            .values()
            .all(|resolution| matches!(resolution, Resolution::Pending { .. })),
        "nothing is published while the third is resolving"
    );
    assert!(state.open.is_empty());

    let state = finish(&state, &jobs[2]);
    assert!(matches!(state.resolutions[&failing], Resolution::Failed(_)));
    assert!(matches!(
        state.resolutions[&second],
        Resolution::Resolved { .. }
    ));
    assert!(matches!(
        state.resolutions[&third],
        Resolution::Resolved { .. }
    ));
    assert!(
        generation(&state, second) < generation(&state, third),
        "committed in the order added"
    );
    assert_eq!(state.open, HashSet::from([second]));
}

#[test]
fn an_item_that_stops_waiting_publishes_the_rest_and_a_replacement_publishes_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let (state, [failing, second, third], jobs) = three(folder.path());

    let state = finish(&state, &jobs[0]);
    let state = finish(&state, &jobs[1]);
    let (state, _) = apply(&state, DocCommand::Remove(third));
    assert!(matches!(state.resolutions[&failing], Resolution::Failed(_)));
    assert!(matches!(
        state.resolutions[&second],
        Resolution::Resolved { .. }
    ));
    assert_eq!(state.open, HashSet::from([second]));

    let (state, _, jobs) = three(folder.path());
    let state = finish(&state, &jobs[1]);
    let (state, _) = apply(
        &state,
        DocCommand::Replace {
            workspace: Workspace::default(),
            file: None,
            saved: Saved::default(),
        },
    );
    assert!(state.open.is_empty() && state.resolutions.is_empty());
}

#[test]
fn removing_an_item_releases_its_held_result_while_the_others_still_resolve() {
    let folder = tempfile::tempdir().unwrap();
    let (state, [_, second, _], jobs) = three(folder.path());

    let state = finish(&state, &jobs[1]);
    assert!(state.batches[0].held.contains_key(&second));
    let (state, _) = apply(&state, DocCommand::Remove(second));
    assert!(
        state.batches[0].held.is_empty(),
        "the removed item's Content is not kept until the others finish"
    );
}
