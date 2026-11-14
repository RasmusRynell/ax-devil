use super::*;

/// An open video that has resolved, in a folder the test can break.
fn shown() -> (
    tempfile::TempDir,
    std::path::PathBuf,
    ItemId,
    WorkspaceState,
) {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let video = item(&file);
    let id = video.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![video], true);
    (folder, file, id, finish(&state, &jobs[0]))
}

#[test]
fn a_failed_reload_keeps_the_old_content_and_says_why_until_the_next_one() {
    let (_folder, file, id, state) = shown();
    let before = state.resolutions[&id].clone();
    std::fs::remove_file(&file).unwrap();

    let (reloading, jobs) = apply(&state, DocCommand::Resolve(id));
    assert_eq!(
        reloading.resolutions[&id], before,
        "the viewer's Content stays"
    );
    assert!(reloading.resolving(&id));
    let failed = finish(&reloading, &jobs[0]);
    assert_eq!(failed.resolutions[&id], before);
    assert!(matches!(
        failed.reloads.get(&id),
        Some(Reload::Failed { .. })
    ));

    // Reading again clears the old news, and a file that is back reads: the Content is new and the news is gone.
    std::fs::write(&file, "x").unwrap();
    let (again, jobs) = apply(&failed, DocCommand::Resolve(id));
    assert!(matches!(
        again.reloads.get(&id),
        Some(Reload::Running { .. })
    ));
    let reloaded = finish(&again, &jobs[0]);
    assert!(generation(&reloaded, id) > generation(&state, id));
    assert!(reloaded.reloads.is_empty());
}

#[test]
fn the_count_of_source_changes_only_grows_across_reloads_so_a_new_change_is_never_taken_for_a_dismissed_one()
 {
    let (_folder, file, id, state) = shown();
    let entry = crate::ContentId::of_item(&id);
    let (offered, _) = apply(
        &state,
        DocCommand::SourceChanged(id, vec![entry.clone()], true),
    );
    assert_eq!(offered.changed[&id].entries[&entry], 1);
    assert_eq!(offered.changed[&id].revision, 1);

    // Reload fails: the count stays, so the viewer's dismissal of the offer at 1 still means one change.
    std::fs::remove_file(&file).unwrap();
    let (reloading, jobs) = apply(&offered, DocCommand::Resolve(id));
    let failed = finish(&reloading, &jobs[0]);
    assert_eq!(failed.changed[&id].revision, 1);
    assert!(matches!(
        failed.reloads.get(&id),
        Some(Reload::Failed { .. })
    ));
    // A change after it is a second one, and the offer takes the place of the failure's news.
    let (again, _) = apply(
        &failed,
        DocCommand::SourceChanged(id, vec![entry.clone()], true),
    );
    assert_eq!(again.changed[&id].revision, 2);
    assert!(again.reloads.is_empty());

    // A change during a reload counts beyond what the reload read: it fails without news, or succeeds and keeps it.
    let (reloading, jobs) = apply(&again, DocCommand::Resolve(id));
    let (during, _) = apply(
        &reloading,
        DocCommand::SourceChanged(id, vec![entry.clone()], true),
    );
    assert_eq!(during.changed[&id].revision, 3);
    let failed = finish(&during, &jobs[0]);
    assert!(failed.reloads.is_empty() && failed.changed[&id].revision == 3);
    std::fs::write(&file, "x").unwrap();
    let (reloading, jobs) = apply(&failed, DocCommand::Resolve(id));
    let (during, _) = apply(
        &reloading,
        DocCommand::SourceChanged(id, vec![entry.clone()], true),
    );
    let reloaded = finish(&during, &jobs[0]);
    assert_eq!(reloaded.changed[&id].revision, 1);
    assert_eq!(reloaded.changed[&id].entries[&entry], 1);
    assert!(generation(&reloaded, id) > generation(&state, id));
}
