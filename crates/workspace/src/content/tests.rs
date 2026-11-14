use super::*;

#[test]
fn only_a_content_or_entry_id_deserializes() {
    let item = ItemId::generate();
    for id in [
        ContentId::of_item(&item),
        ContentId::of_playlist(&item, "Runs"),
        ContentId::of_entry(&item, "Runs", "SEQ-01"),
    ] {
        let text = serde_json::to_value(&id).unwrap();
        assert_eq!(serde_json::from_value::<ContentId>(text).unwrap(), id);
    }
    for bad in ["", "SEQ-01", "not-an-item/SEQ-01", &format!("{item}/")] {
        assert!(
            serde_json::from_value::<ContentId>(bad.into()).is_err(),
            "{bad:?}"
        );
    }
}

fn playlist(name: &str, entries: &[&str]) -> handler_api::ResolvedPlaylist {
    handler_api::ResolvedPlaylist {
        name: name.into(),
        entries: entries
            .iter()
            .map(|entry| handler_api::ResolvedEntry {
                name: (*entry).into(),
                video: VideoRef::File(format!("/{entry}.mp4").into()),
                overlays: Vec::new(),
                starts_hidden: false,
                lanes_starting_hidden: Vec::new(),
                metadata: Default::default(),
            })
            .collect(),
        matches: None,
        sequence_groups: None,
        metadata: Default::default(),
    }
}

fn build(playlists: Vec<handler_api::ResolvedPlaylist>) -> Result<PlaylistContent, UserError> {
    let resolved = ResolvedPlaylists {
        playlists,
        warnings: Vec::new(),
    };
    playlist_content(&ItemId::generate(), "Runs", resolved)
}

#[test]
fn a_playlist_rebound_to_another_item_has_the_ids_it_would_have_been_resolved_with() {
    let resolved = || ResolvedPlaylists {
        playlists: vec![playlist("run", &["a", "b"]), playlist("x/y", &["z"])],
        warnings: Vec::new(),
    };
    let (first, second) = (ItemId::generate(), ItemId::generate());
    let content = playlist_content(&first, "Runs", resolved()).unwrap();
    assert_eq!(
        content.rebound(&second),
        playlist_content(&second, "Runs", resolved()).unwrap()
    );
}

#[test]
fn shared_names_are_numbered_around_the_suffixes_already_there() {
    let names = numbered(["a", "a", "a (2)", "b", "a", "A"]);
    assert_eq!(names, ["a", "a (3)", "a (2)", "b", "a (4)", "A"]);

    // Linear in the number of names: this would take half a minute if every duplicate searched from 2.
    let many = numbered(vec!["run"; 20_000]);
    assert_eq!(many[19_999], "run (20000)");
    assert_eq!(many.iter().collect::<HashSet<_>>().len(), 20_000);
}

#[test]
fn several_playlists_give_ids_by_playlist_name_and_refuse_what_has_no_clear_identity() {
    let content = build(vec![
        playlist("run", &["a", "b"]),
        playlist("run", &["a"]),
        playlist("x/y", &["z", "z"]),
    ])
    .unwrap();
    let spans: Vec<_> = content
        .playlists
        .iter()
        .map(|span| (span.name.as_str(), span.entries))
        .collect();
    assert_eq!(spans, [("run", 2), ("run (2)", 1), ("x/y", 2)]);
    assert_eq!(content.display_name, "Runs");
    let item = content.item_id;
    let ids: Vec<_> = content
        .entries
        .iter()
        .map(|entry| entry.id.clone())
        .collect();
    assert_eq!(
        ids,
        [
            ContentId::of_entry(&item, "run", "a"),
            ContentId::of_entry(&item, "run", "b"),
            ContentId::of_entry(&item, "run (2)", "a"),
            ContentId::of_entry(&item, "x/y", "z"),
            ContentId::of_entry(&item, "x/y", "z (2)"),
        ]
    );
    assert_eq!(content.entries[4].label, "z (2)");

    let reason = |playlists| build(playlists).unwrap_err().reason;
    assert_eq!(
        reason(vec![playlist("run", &["a"]), playlist("empty", &[])]),
        "Playlists require at least one entry."
    );
    // `run/a/b` names both the entry `a/b` of `run` and the entry `b` of `run/a`.
    assert!(reason(vec![playlist("run", &["a/b"]), playlist("run/a", &["b"])]).ends_with(": b"));
    let mut lanes = playlist("run", &["a"]);
    let overlay = handler_api::OverlayRef {
        label: "GT".into(),
        slot: "gt".into(),
        source: handler_api::LaneSource::File("/o.txt".into()),
        handler: "MOT_FILE".into(),
        options: Default::default(),
        metadata: Default::default(),
    };
    lanes.entries[0].overlays = vec![overlay.clone(), overlay];
    assert!(reason(vec![lanes]).ends_with(": GT"));
    // "a/b" + "c" and "a" + "b/c" are the same id.
    assert!(reason(vec![playlist("a", &["b/c"]), playlist("a/b", &["c"])]).ends_with(": c"));
}
