use super::*;

/// The Content of a playlist item with one playlist of these entries, each with whether it starts hidden.
fn playlist_of(item: &WorkspaceItem, entries: &[(&str, bool)]) -> workspace::Content {
    let entries: Vec<_> = entries
        .iter()
        .map(|&(label, starts_hidden)| workspace::Entry {
            id: workspace::ContentId::of_entry(&item.id, "P", label),
            label: label.to_owned(),
            video: workspace::VideoRef::File(format!("/{label}.mp4").into()),
            overlays: Vec::new(),
            starts_hidden,
            lanes_starting_hidden: Vec::new(),
        })
        .collect();
    workspace::Content::Playlist(workspace::PlaylistContent {
        id: workspace::ContentId::of_item(&item.id),
        item_id: item.id,
        display_name: "P".into(),
        playlists: vec![workspace::PlaylistSpan {
            id: workspace::ContentId::of_playlist(&item.id, "P"),
            name: "P".into(),
            entries: entries.len(),
            matches: None,
            sequence_groups: None,
        }],
        entries,
        warnings: Vec::new(),
    })
}

#[test]
fn the_users_choice_beats_how_the_resolver_started_an_entry_until_the_entry_is_gone() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    // A playlist item, whatever its recipe, takes the Content it is completed with.
    let (video, runs) = (item(&file), item(&file));
    let (state, jobs) = add(
        &WorkspaceState::default(),
        vec![video.clone(), runs.clone()],
        false,
    );
    let state = finish(&state, &jobs[0]);
    let resolve = |state: &WorkspaceState, entries: &[(&str, bool)]| {
        let (state, jobs) = apply(state, DocCommand::Resolve(runs.id));
        complete(
            &state,
            runs.id,
            jobs[0].attempt,
            Ok(playlist_of(&runs, entries)),
        )
    };
    let set = |state: &WorkspaceState, target: HiddenRef, hidden: bool| {
        apply(state, DocCommand::SetHidden { target, hidden }).0
    };
    let entry = |label: &str| workspace::ContentId::of_entry(&runs.id, "P", label);
    let shown = |state: &WorkspaceState, label: &str| {
        let Resolution::Resolved { content, .. } = &state.resolutions[&runs.id] else {
            panic!("the playlist resolved");
        };
        !state.entry_hidden(content.entry(&entry(label)).unwrap())
    };

    let first = Ok(playlist_of(&runs, &[("a", true), ("b", false)]));
    let state = complete(&state, runs.id, jobs[1].attempt, first);
    assert!(
        !shown(&state, "a") && shown(&state, "b"),
        "the resolver starts a hidden"
    );

    let unhidden = set(&state, HiddenRef::Entry(entry("a")), false);
    let again = resolve(&unhidden, &[("a", true), ("b", false)]);
    assert!(shown(&again, "a"), "showing it survives resolving again");

    let gone = resolve(&again, &[("b", false)]);
    let back = resolve(&gone, &[("a", true), ("b", false)]);
    assert!(
        !shown(&back, "a"),
        "an entry that was gone starts as its resolver says"
    );

    let own = HiddenRef::Entry(workspace::ContentId::of_item(&video.id));
    assert_eq!(
        set(&state, own, true),
        state,
        "a video's own entry cannot be hidden"
    );
}
