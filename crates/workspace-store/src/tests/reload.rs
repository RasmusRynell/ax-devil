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
fn a_reload_that_fails_after_the_files_changed_again_leaves_the_offer_to_reload() {
    let (_folder, file, id, state) = shown();
    std::fs::remove_file(&file).unwrap();
    let (reloading, jobs) = apply(&state, DocCommand::Resolve(id));
    let (changed, _) = apply(&reloading, DocCommand::SourceChanged(id));
    assert_eq!(changed.changed[&id], 1);
    let failed = finish(&changed, &jobs[0]);
    assert!(failed.reloads.is_empty() && failed.changed[&id] == 1);

    // The offer stands in place of an earlier failure's news.
    let (retry, jobs) = apply(&failed, DocCommand::Resolve(id));
    let failed = finish(&retry, &jobs[0]);
    assert!(matches!(
        failed.reloads.get(&id),
        Some(Reload::Failed { .. })
    ));
    let (offered, _) = apply(&failed, DocCommand::SourceChanged(id));
    assert!(offered.reloads.is_empty());
}
