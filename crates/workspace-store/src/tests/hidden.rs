use super::*;

/// The Content of a playlist item with one playlist of these entries, each with one lane `L`; whether an entry starts
/// hidden is also whether its lane does.
fn playlist_of(item: &WorkspaceItem, entries: &[(&str, bool)]) -> workspace::Content {
    let entries: Vec<_> = entries
        .iter()
        .map(|&(label, starts_hidden)| workspace::Entry {
            id: workspace::ContentId::of_entry(&item.id, "P", label),
            label: label.to_owned(),
            video: workspace::VideoRef::File(format!("/{label}.mp4").into()),
            overlays: vec![workspace::OverlayRef {
                label: "L".into(),
                slot: "L".into(),
                path: "/o.txt".into(),
                handler: "MOT_FILE".into(),
                options: Default::default(),
                metadata: Default::default(),
            }],
            starts_hidden,
            lanes_starting_hidden: if starts_hidden {
                vec!["L".into()]
            } else {
                Vec::new()
            },
            metadata: Default::default(),
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
            metadata: Default::default(),
        }],
        entries,
        warnings: Vec::new(),
        watched: Vec::new(),
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

#[test]
fn an_entry_and_lane_that_survive_resolving_again_keep_their_visibility_whatever_the_resolver_now_starts_them_as()
 {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("gate.mp4");
    std::fs::write(&file, "x").unwrap();
    let runs = item(&file);
    let (state, jobs) = add(&WorkspaceState::default(), vec![runs.clone()], false);
    let first = Ok(playlist_of(&runs, &[("a", true), ("b", false)]));
    let state = complete(&state, runs.id, jobs[0].attempt, first);
    let (state, jobs) = apply(&state, DocCommand::Resolve(runs.id));
    let changed = Ok(playlist_of(
        &runs,
        &[("a", false), ("b", true), ("c", true)],
    ));
    let state = complete(&state, runs.id, jobs[0].attempt, changed);

    let Resolution::Resolved { content, .. } = &state.resolutions[&runs.id] else {
        panic!("the playlist resolved");
    };
    // Entry and lane hidden, as for entries a, b, c.
    let hidden: Vec<_> = content
        .entries()
        .iter()
        .map(|entry| {
            let lane = HiddenRef::Lane(entry.id.clone(), "L".into());
            (
                state.entry_hidden(entry),
                state.is_hidden(&lane, entry.lanes_starting_hidden.contains(&"L".to_owned())),
            )
        })
        .collect();
    assert_eq!(hidden, [(true, true), (false, false), (true, true)]);

    let (removed, _) = apply(&state, DocCommand::Remove(runs.id));
    assert!(
        removed.hidden.is_empty(),
        "removing the item forgets its choices"
    );
}
