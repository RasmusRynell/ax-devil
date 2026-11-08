use super::*;

#[test]
fn an_edit_keeps_the_item_and_resolves_again_only_when_the_recipe_changed() {
    let folder = tempfile::tempdir().unwrap();
    let (first, second) = (folder.path().join("a.mp4"), folder.path().join("b.mp4"));
    std::fs::write(&first, "x").unwrap();
    std::fs::write(&second, "x").unwrap();
    let (kept, edited) = (item(&first), item(&first));
    let (kept_id, edited_id) = (kept.id, edited.id);
    let (state, jobs) = add(
        &WorkspaceState::default(),
        vec![kept, edited.clone()],
        false,
    );
    let mut state = jobs.iter().fold(state, |state, job| finish(&state, job));
    state.open.insert(edited_id);
    let before = generation(&state, edited_id);
    let hide = DocCommand::SetHidden {
        target: HiddenRef::Lane(content_id(&state, edited_id), "lane".into()),
        hidden: true,
    };
    let (state, _) = apply(&state, hide);
    let mark = BookmarkCommand::Add {
        id: foundation::BookmarkId::generate(),
        item: edited_id,
        entry: workspace::ContentId::of_item(&edited_id),
        frame: 3,
        time: foundation::MediaTime::from_us(120_000),
    };
    let review = BookmarkCommand::SetReviewed {
        target: workspace::BookmarkTarget {
            item: edited_id,
            entry: None,
        },
        reviewed: true,
    };
    let (state, _) = apply(&state, DocCommand::Bookmarks(mark));
    let (state, _) = apply(&state, DocCommand::Bookmarks(review));
    assert_eq!(state.workspace.bookmarks.len(), 1);
    assert_eq!(state.workspace.reviewed.len(), 1);
    let edit = |state: &WorkspaceState, changed: WorkspaceItem| {
        apply(
            state,
            DocCommand::EditItem {
                item: Box::new(changed),
                content: None,
            },
        )
    };

    let (same, jobs) = edit(&state, edited.clone());
    assert!(
        (same == state) && jobs.is_empty(),
        "an equal edit does nothing"
    );

    let renamed = WorkspaceItem {
        label: "Gate".into(),
        ..edited.clone()
    };
    let (named, jobs) = edit(&state, renamed.clone());
    assert!(jobs.is_empty(), "a new name resolves nothing");
    assert_eq!(named.workspace.items[1].label, "Gate");
    assert_eq!(generation(&named, edited_id), before);
    assert_eq!(
        named.hidden, state.hidden,
        "a new name keeps what was hidden"
    );

    let (again, jobs) = apply(
        &state,
        DocCommand::RenameItem {
            item: edited_id,
            label: " Gate ".into(),
        },
    );
    assert!(
        jobs.is_empty() && again == named,
        "renaming is an edit of the name alone"
    );

    let moved = WorkspaceItem {
        recipe: item(&second).recipe,
        ..renamed
    };
    let (moved_state, jobs) = edit(&named, moved.clone());
    assert_eq!(jobs.len(), 1, "a new recipe resolves again");
    assert!(moved_state.hidden.is_empty() && moved_state.open.contains(&edited_id));
    let ids: Vec<_> = moved_state
        .workspace
        .items
        .iter()
        .map(|item| item.id)
        .collect();
    assert_eq!(ids, [kept_id, edited_id], "the item keeps its place");
    let moved_state = finish(&moved_state, &jobs[0]);
    assert!(generation(&moved_state, edited_id) > before);
    assert_eq!(
        (
            &moved_state.workspace.bookmarks,
            &moved_state.workspace.reviewed
        ),
        (&state.workspace.bookmarks, &state.workspace.reviewed),
        "a new recipe keeps the marks and the review state"
    );

    let playlist = WorkspaceItem {
        recipe: workspace::Recipe::new(Source::Playlist(workspace::PlaylistRecipe {
            resolver: "mot_challenge".into(),
            settings: Default::default(),
        })),
        ..moved
    };
    let (other_kind, jobs) = edit(&moved_state, playlist);
    assert!(
        (other_kind == moved_state) && jobs.is_empty(),
        "an item keeps its kind"
    );
}
