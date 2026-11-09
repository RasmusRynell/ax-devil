use super::*;

fn feed(op: Op, line: &str, count: usize) -> Option<String> {
    let mut collected = Collected::new(op);
    (1..=count + 1).find_map(|number| match collected.line(number, line.as_bytes()) {
        Next::Stop(reason) => Some(reason),
        Next::Continue | Next::Done | Next::SinkFull => None,
    })
}

#[test]
fn warnings_past_the_cap_are_summarized_but_choices_and_entries_stop_the_run() {
    let warning = r#"{"warning": {"problem": "p", "consequence": "c"}}"#;
    let choice = r#"{"choice": {"value": "a"}}"#;
    let entry =
        |n: usize| format!(r#"{{"entry": {{"name": "e{n}", "video": {{"file": "/v.mp4"}}}}}}"#);
    let mut collected = Collected::new(Op::Resolve);
    for number in 1..=MAX_WARNINGS + 10 {
        assert!(matches!(
            collected.line(number, warning.as_bytes()),
            Next::Continue
        ));
    }
    assert!(matches!(
        collected.line(MAX_WARNINGS + 11, entry(0).as_bytes()),
        Next::Continue
    ));
    let resolved = collected.into_playlists("fallback");
    assert_eq!(resolved.playlists[0].entries.len(), 1);
    assert_eq!(resolved.warnings.len(), MAX_WARNINGS + 1);
    assert_eq!(
        resolved.warnings[MAX_WARNINGS].problem,
        "10 more warnings suppressed"
    );

    assert_eq!(
        feed(Op::Scan, choice, MAX_CHOICES).as_deref(),
        Some("line 50001: more than 50000 choices")
    );
    let mut collected = Collected::new(Op::Resolve);
    let stopped =
        (1..=MAX_ENTRIES + 1).find_map(|n| match collected.line(n, entry(n).as_bytes()) {
            Next::Stop(reason) => Some(reason),
            Next::Continue | Next::Done | Next::SinkFull => None,
        });
    assert_eq!(
        stopped.as_deref(),
        Some("line 50001: more than 50000 entries and playlists")
    );
}

#[test]
fn metadata_of_a_playlist_an_entry_and_an_overlay_is_carried() {
    let lines = [
        r#"{"playlist": {"name": "p", "metadata": {"owner": "lab"}}}"#,
        r#"{"entry": {"name": "e", "video": {"file": "/v.mp4"}, "metadata": {"split": "train"},
            "overlays": [{"label": "GT", "slot": "gt", "path": "/gt.txt", "handler": "MOT_FILE",
            "metadata": {"detector": true}}]}}"#,
    ];
    let mut collected = Collected::new(Op::Resolve);
    for (number, line) in lines.iter().enumerate() {
        let line = line.split_whitespace().collect::<Vec<_>>().join(" ");
        assert!(matches!(
            collected.line(number + 1, line.as_bytes()),
            Next::Continue
        ));
    }
    let playlist = collected.into_playlists("resolver").playlists.remove(0);
    let entry = &playlist.entries[0];
    assert_eq!(playlist.metadata["owner"], "lab");
    assert_eq!(entry.metadata["split"], "train");
    assert_eq!(entry.overlays[0].metadata["detector"], true);
}

#[test]
fn metadata_is_kept_to_its_budget_and_a_plugin_over_it_is_stopped() {
    use crate::budget::{Budget, OBJECT_BYTES, RUN_BYTES};
    // `{"k":"xxx"}` is eight bytes longer than its text.
    let object =
        |bytes: usize| Settings::from_iter([("k".to_owned(), "x".repeat(bytes - 8).into())]);
    let mut budget = Budget::default();
    budget.take(&object(OBJECT_BYTES)).unwrap();
    assert!(
        budget.take(&object(OBJECT_BYTES + 1)).is_err(),
        "one byte over an object"
    );

    // Exactly the run's bytes fit, and one more does not; a refused object takes nothing.
    let mut budget = Budget::default();
    budget.take(&object(100)).unwrap();
    for _ in 0..127 {
        budget.take(&object(OBJECT_BYTES)).unwrap();
    }
    let rest = RUN_BYTES - 100 - 127 * OBJECT_BYTES;
    assert!(
        budget.take(&object(rest + 1)).is_err(),
        "one byte over the run"
    );
    budget.take(&object(rest)).unwrap();
    assert!(
        budget.take(&object(8)).is_err(),
        "nothing fits after the run is full"
    );

    // One value larger than the whole object budget stops the run at its line.
    let big = "x".repeat(OBJECT_BYTES);
    let line = format!(r#"{{"playlist": {{"name": "p", "metadata": {{"k": "{big}"}}}}}}"#);
    let mut collected = Collected::new(Op::Resolve);
    let Next::Stop(reason) = collected.line(7, line.as_bytes()) else {
        panic!("an oversized metadata value is refused");
    };
    assert!(
        reason.starts_with("line 7: a metadata object is "),
        "{reason}"
    );
}

#[test]
fn playlist_lines_split_the_entries_and_entries_before_the_first_one_take_the_resolvers_name() {
    let entry =
        |name: &str| format!(r#"{{"entry": {{"name": "{name}", "video": {{"file": "/v.mp4"}}}}}}"#);
    let playlist = |name: &str| format!(r#"{{"playlist": {{"name": "{name}"}}}}"#);
    let run = |lines: &[String]| {
        let mut collected = Collected::new(Op::Resolve);
        for (number, line) in lines.iter().enumerate() {
            if let Next::Stop(reason) = collected.line(number + 1, line.as_bytes()) {
                return Err(reason);
            }
        }
        Ok(collected.into_playlists("resolver"))
    };

    let resolved = run(&[
        entry("a"),
        playlist("p"),
        entry("a"),
        entry("b"),
        playlist("p"),
    ])
    .unwrap();
    let shape: Vec<_> = resolved
        .playlists
        .iter()
        .map(|playlist| {
            let names: Vec<_> = playlist.entries.iter().map(|e| e.name.as_str()).collect();
            (playlist.name.as_str(), names)
        })
        .collect();
    assert_eq!(
        shape,
        [
            ("resolver", vec!["a"]),
            ("p", vec!["a", "b"]),
            ("p", vec![])
        ]
    );

    assert_eq!(
        run(&[playlist("")]).err().as_deref(),
        Some("line 1: the playlist name is empty")
    );
}

#[test]
fn optional_counts_and_considered_flags_read_into_the_playlist_and_default_to_shown() {
    let overlay = |label: &str, extra: &str| {
        format!(
            r#"{{"label": "{label}", "slot": "{label}", "path": "/o.txt", "handler": "MOT_FILE"{extra}}}"#
        )
    };
    let lines = [
        r#"{"playlist": {"name": "p", "matches": 3, "sequence_groups": 2}}"#.to_owned(),
        format!(
            r#"{{"entry": {{"name": "a", "video": {{"file": "/a.mp4"}}, "considered": false, "overlays": [{}, {}]}}}}"#,
            overlay("L1", r#", "considered": false"#),
            overlay("L2", ""),
        ),
        r#"{"playlist": {"name": "old"}}"#.to_owned(),
        r#"{"entry": {"name": "b", "video": {"file": "/b.mp4"}}}"#.to_owned(),
    ];
    let mut collected = Collected::new(Op::Resolve);
    for (number, line) in lines.iter().enumerate() {
        assert!(matches!(
            collected.line(number + 1, line.as_bytes()),
            Next::Continue
        ));
    }
    let [p, old] = &collected.into_playlists("resolver").playlists[..] else {
        panic!("two playlists");
    };
    assert_eq!((p.matches, p.sequence_groups), (Some(3), Some(2)));
    assert_eq!((old.matches, old.sequence_groups), (None, None));
    let ([a], [b]) = (&p.entries[..], &old.entries[..]) else {
        panic!("one entry each");
    };
    assert!(a.starts_hidden && !b.starts_hidden);
    assert_eq!(a.lanes_starting_hidden, ["L1"]);
    assert!(b.lanes_starting_hidden.is_empty());

    let too_many = format!(
        r#"{{"playlist": {{"name": "p", "matches": {}}}}}"#,
        MAX_COUNT + 1
    );
    assert_eq!(
        feed(Op::Resolve, &too_many, 0).as_deref(),
        Some("line 1: 'matches' is more than 9007199254740991")
    );
}
