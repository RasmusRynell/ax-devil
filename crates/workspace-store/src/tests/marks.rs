use super::*;
use foundation::{BookmarkId, MediaTime};
use workspace::{Content, ContentId};

#[test]
fn a_playlist_listing_its_entries_in_another_order_keeps_the_marks_on_their_entries() {
    let folder = tempfile::tempdir().unwrap();
    for name in ["SEQ-01", "SEQ-02"] {
        std::fs::create_dir_all(folder.path().join(name).join("gt")).unwrap();
        std::fs::write(folder.path().join(name).join("gt/gt.txt"), "").unwrap();
        std::fs::write(
            folder.path().join(name).join("seqinfo.ini"),
            "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
        )
        .unwrap();
    }
    let mut settings = serde_json::Map::new();
    settings.insert("root".into(), folder.path().display().to_string().into());
    let playlist =
        workspace::intake::playlist("mot_challenge", settings, &Handlers::built_in()).unwrap();
    let id = playlist.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![playlist], false);
    let state = finish(&state, &jobs[0]);
    let mark = |state: &WorkspaceState, entry: &str| {
        let command = BookmarkCommand::Add {
            id: BookmarkId::generate(),
            item: id,
            entry: ContentId::of_entry(&id, "MOT Challenge", entry),
            frame: 3,
            time: MediaTime::from_us(120_000),
        };
        apply(state, DocCommand::Bookmarks(command)).0
    };
    let state = mark(&mark(&state, "SEQ-02"), "SEQ-01");
    let listed = |state: &WorkspaceState| {
        bookmark_groups(state)
            .iter()
            .map(|group| (group.entry.clone(), group.standing != Standing::Missing))
            .collect::<Vec<_>>()
    };
    let before = listed(&state);

    let (state, jobs) = apply(&state, DocCommand::Resolve(id));
    let Ok(Content::Playlist(mut reordered)) = resolve(
        &jobs[0],
        &Handlers::built_in(),
        &Environment::of_process(),
        &CancelToken::new(),
    ) else {
        panic!("the playlist resolves again");
    };
    reordered.entries.reverse();
    let state = complete(
        &state,
        id,
        jobs[0].attempt,
        Ok(Content::Playlist(reordered)),
    );

    let mut after = listed(&state);
    after.reverse();
    assert_eq!(before, after, "the groups follow the entries, none missing");
}

#[test]
fn editing_the_recipe_of_a_video_keeps_its_marks() {
    let folder = tempfile::tempdir().unwrap();
    let (first, second) = (folder.path().join("a.mp4"), folder.path().join("b.mp4"));
    std::fs::write(&first, "x").unwrap();
    std::fs::write(&second, "x").unwrap();
    let video = item(&first);
    let id = video.id;
    let (state, jobs) = add(&WorkspaceState::default(), vec![video], false);
    let state = finish(&state, &jobs[0]);
    let mark = BookmarkCommand::Add {
        id: BookmarkId::generate(),
        item: id,
        entry: ContentId::of_item(&id),
        frame: 3,
        time: MediaTime::from_us(120_000),
    };
    let (state, _) = apply(&state, DocCommand::Bookmarks(mark));
    let reviewed = BookmarkCommand::SetReviewed {
        target: workspace::BookmarkTarget {
            item: id,
            entry: None,
        },
        reviewed: true,
    };
    let (state, _) = apply(&state, DocCommand::Bookmarks(reviewed));

    let moved = WorkspaceItem {
        recipe: item(&second).recipe,
        ..state.workspace.items[0].clone()
    };
    let (state, jobs) = apply(
        &state,
        DocCommand::EditItem {
            item: Box::new(moved),
            content: None,
        },
    );
    let state = finish(&state, &jobs[0]);

    let [group] = bookmark_groups(&state).try_into().unwrap();
    assert!(group.reviewed && group.bookmarks.len() == 1 && group.standing != Standing::Missing);
}
