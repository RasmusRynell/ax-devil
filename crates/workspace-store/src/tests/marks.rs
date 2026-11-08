use super::*;
use foundation::{BookmarkId, MediaTime};
use workspace::{Content, ContentId};

#[test]
fn a_playlist_listing_its_entries_in_another_order_keeps_each_mark_on_its_entry() {
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
    // A different frame on each entry, so a mark that moved to the other entry shows.
    let mark = |state: &WorkspaceState, entry: &str, frame| {
        let command = BookmarkCommand::Add {
            id: BookmarkId::generate(),
            item: id,
            entry: ContentId::of_entry(&id, "MOT Challenge", entry),
            frame,
            time: MediaTime::from_us(120_000),
        };
        apply(state, DocCommand::Bookmarks(command)).0
    };
    let state = mark(&mark(&state, "SEQ-02", 7), "SEQ-01", 3);
    let before = bookmark_groups(&state);
    assert_eq!(before.len(), 2);

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

    let mut after = bookmark_groups(&state);
    after.reverse();
    assert_eq!(
        before, after,
        "the groups follow the entries, each with its own marks, none missing"
    );
}
