use super::*;
use foundation::{BookmarkId, MediaTime};
use workspace::BookmarkTarget;

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

#[test]
fn removing_an_item_clears_only_its_bookmarks_and_reviewed_marks() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let (kept, removed) = (item(&file), item(&file));
    let (kept_id, removed_id) = (kept.id, removed.id);
    let (state, jobs) = add(&WorkspaceState::default(), vec![kept, removed], true);
    let mut state = jobs.iter().fold(state, |state, job| finish(&state, job));
    for id in [kept_id, removed_id] {
        let entry = content_id(&state, id);
        let marks = [
            BookmarkCommand::Add {
                id: BookmarkId::generate(),
                item: id,
                entry,
                frame: 3,
                time: MediaTime(120_000),
            },
            BookmarkCommand::SetReviewed {
                target: BookmarkTarget {
                    item: id,
                    entry: None,
                },
                reviewed: true,
            },
        ];
        for mark in marks {
            state = apply(&state, DocCommand::Bookmarks(mark)).0;
        }
    }
    assert_eq!(state.workspace.bookmarks.len(), 2);
    assert_eq!(state.workspace.reviewed.len(), 2);

    let (gone, _) = apply(&state, DocCommand::Remove(removed_id));

    let bookmarked: Vec<_> = gone
        .workspace
        .bookmarks
        .iter()
        .map(|b| b.target.item)
        .collect();
    let reviewed: Vec<_> = gone.workspace.reviewed.iter().map(|t| t.item).collect();
    assert_eq!((bookmarked, reviewed), (vec![kept_id], vec![kept_id]));
}
