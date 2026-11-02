//! Querying the history over a placement: class toggles, search, order, pages and where objects are on screen.

use foundation::{MediaTime, Timeline};
use overlay_sync::{
    KeyChunks, MatchBasis, MatchPolicy, SampleKeys, Selection, Sticky, Tolerance, select,
};

use super::{
    history::{history_of, policy_of, scene_of, scenes},
    *,
};
use crate::{History, Op, Placement, Query, Search, Sort, SortKey, Term, query::Record};

/// A record of the fixture: its name, fields and free text.
type Case = (String, Vec<(String, Vec<String>)>, Vec<String>);

fn timeline(frames: usize) -> Timeline {
    let times = (0..frames).map(|n| MediaTime(n as i64 * 40_000)).collect();
    Timeline::new(times).expect("a timeline")
}

fn place(keys: &SampleKeys, frames: usize, coverage: Coverage, policy: &MatchPolicy) -> Placement {
    Placement::new(
        keys,
        coverage,
        &timeline(frames),
        policy,
        &CancelToken::new(),
    )
    .expect("not cancelled")
}

/// Sample `n` is shown by frame `n`, of `frames` frames.
fn one_to_one(frames: usize) -> Placement {
    let keys: Vec<u64> = (0..frames as u64).collect();
    place(
        &frame_keys(&keys),
        frames,
        Coverage::Dense,
        &MatchPolicy::default(),
    )
}

fn search(path: Option<&str>, value: &str) -> Search {
    Search {
        groups: vec![vec![Term {
            path: path.map(str::to_string),
            op: Op::Eq,
            value: value.to_string(),
            negated: false,
        }]],
    }
}

fn query(enabled: &[u32], search: Search, key: SortKey, descending: bool) -> Query {
    Query {
        enabled: enabled.to_vec(),
        search,
        sort: Sort { key, descending },
    }
}

/// The ids a query lists, in order.
fn listed(history: &History, placement: &Placement, query: &Query) -> Vec<String> {
    let matches = query
        .run(history, placement, &CancelToken::new())
        .expect("not cancelled");
    let rows = matches.rows(history, placement, 0, usize::MAX, 0);
    assert_eq!(rows.len() as u64, matches.matched());
    rows.into_iter().map(|row| row.id).collect()
}

fn ids(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| item.to_string()).collect()
}

#[test]
fn the_shared_search_cases_match_the_records_the_reference_matched() {
    let file: serde_json::Value =
        serde_json::from_str(include_str!("../../../../fixtures/search/cases.json"))
            .expect("the fixture is JSON");
    let text = |value: &serde_json::Value| -> Vec<String> {
        value
            .as_array()
            .expect("a list")
            .iter()
            .map(|item| item.as_str().expect("text").to_string())
            .collect()
    };
    let records: Vec<Case> = file["records"]
        .as_object()
        .expect("records")
        .iter()
        .map(|(name, record)| {
            let fields = record["fields"]
                .as_object()
                .expect("fields")
                .iter()
                .map(|(key, values)| (key.clone(), text(values)))
                .collect();
            (name.clone(), fields, text(&record["text"]))
        })
        .collect();

    let mut wrong = Vec::new();
    for case in file["cases"].as_array().expect("cases") {
        let groups = case["groups"]
            .as_array()
            .expect("groups")
            .iter()
            .map(|group| {
                group
                    .as_array()
                    .expect("a group")
                    .iter()
                    .map(|term| Term {
                        path: term["path"].as_str().map(str::to_string),
                        op: match term["op"].as_str().expect("an op") {
                            "Eq" => Op::Eq,
                            "Gt" => Op::Gt,
                            "Lt" => Op::Lt,
                            "Ge" => Op::Ge,
                            "Le" => Op::Le,
                            other => panic!("unknown op {other}"),
                        },
                        value: term["value"].as_str().expect("a value").to_string(),
                        negated: term["negated"].as_bool().expect("negated"),
                    })
                    .collect()
            })
            .collect();
        let compiled = Search { groups }.compile();
        let mut matched: Vec<String> = records
            .iter()
            .filter(|(_, fields, texts)| {
                compiled.matches(&Record {
                    fields: fields
                        .iter()
                        .map(|(key, values)| {
                            (key.as_str(), values.iter().map(String::as_str).collect())
                        })
                        .collect(),
                    text: texts.iter().map(String::as_str).collect(),
                })
            })
            .map(|(name, ..)| name.clone())
            .collect();
        let mut expected = text(&case["matches"]);
        matched.sort();
        expected.sort();
        if matched != expected {
            wrong.push(format!(
                "{}: {matched:?}, wanted {expected:?}",
                case["text"]
            ));
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

#[test]
fn class_toggles_judge_every_class_an_object_was_recorded_with() {
    let policy = policy_of(&[("show_car", "car"), ("show_bus", "bus")]);
    let history = history_of(
        &[
            scene_of(
                0,
                &[
                    ("both", &["car"]),
                    ("car", &["car"]),
                    ("bus", &["bus"]),
                    ("odd", &["zebra"]),
                    ("none", &[]),
                ],
            ),
            scene_of(1, &[("both", &["bus"])]),
        ],
        &policy,
    );
    let placement = one_to_one(2);
    let kept = |enabled: &[u32]| {
        listed(
            &history,
            &placement,
            &query(enabled, Search::default(), SortKey::Id, false),
        )
    };

    assert_eq!(kept(&[0b11]), ids(&["both", "bus", "car", "none", "odd"]));
    // Car then bus is judged as both: it stays under either; a class no option knows stays while any is on.
    assert_eq!(kept(&[0b01]), ids(&["both", "car", "none", "odd"]));
    assert_eq!(kept(&[0b10]), ids(&["both", "bus", "none", "odd"]));
    assert!(kept(&[0]).is_empty(), "everything off shows nothing");
    assert!(kept(&[]).is_empty());
    // Bits past the options name nothing.
    assert!(kept(&[0b100]).is_empty());

    let matches = query(&[0b01], search(Some("class"), "car"), SortKey::Id, false)
        .run(&history, &placement, &CancelToken::new())
        .expect("not cancelled");
    assert_eq!(
        (matches.total(), matches.matched()),
        (4, 2),
        "total is what the toggles keep"
    );
}

#[test]
fn search_reads_id_class_and_event_and_lists_only_objects_the_video_shows() {
    let history = history_of(&scenes(), &policy_of(&[]));
    let placement = one_to_one(3);
    let list = |search: Search| {
        listed(
            &history,
            &placement,
            &query(&[], search, SortKey::Id, false),
        )
    };

    assert_eq!(
        list(Search::default()),
        ids(&["a", "b"]),
        "z is only named by an event"
    );
    assert_eq!(list(search(Some("event"), "RENAME")), ids(&["a"]));
    assert_eq!(list(search(Some("class"), "bus")), ids(&["a"]));
    assert_eq!(list(search(Some("id"), "B")), ids(&["b"]));
    assert_eq!(
        list(search(None, "us")),
        ids(&["a"]),
        "free text looks in id and classes"
    );
    assert!(
        list(search(Some("speed"), "1")).is_empty(),
        "an unknown path matches nothing"
    );
}

#[test]
fn rows_are_sorted_by_the_key_then_by_natural_id_and_pages_do_not_overlap() {
    let history = history_of(
        &[
            scene_of(0, &[("other", &["beta"])]),
            scene_of(
                1,
                &[
                    ("id9", &["alpha", "beta"]),
                    ("id2", &["Alpha"]),
                    ("id02", &[]),
                ],
            ),
            scene_of(2, &[]),
            scene_of(3, &[("id10", &["Zeta"])]),
        ],
        &policy_of(&[]),
    );
    let placement = one_to_one(4);
    let order = |key, descending| {
        listed(
            &history,
            &placement,
            &query(&[], Search::default(), key, descending),
        )
    };

    // Digit runs count by value; ids equal in value keep the order they were first seen in.
    assert_eq!(
        order(SortKey::Id, false),
        ids(&["id2", "id02", "id9", "id10", "other"])
    );
    assert_eq!(
        order(SortKey::Id, true),
        ids(&["other", "id10", "id9", "id2", "id02"])
    );
    // Descending flips the key only: ties stay in ascending id order.
    assert_eq!(
        order(SortKey::FirstSeen, false),
        ids(&["other", "id2", "id02", "id9", "id10"])
    );
    assert_eq!(
        order(SortKey::FirstSeen, true),
        ids(&["id10", "id2", "id02", "id9", "other"])
    );
    assert_eq!(
        order(SortKey::Class, false),
        ids(&["id02", "id2", "id9", "other", "id10"])
    );
    assert_eq!(
        order(SortKey::Class, true),
        ids(&["id10", "other", "id9", "id2", "id02"])
    );

    let matches = query(&[], Search::default(), SortKey::Class, false)
        .run(&history, &placement, &CancelToken::new())
        .expect("not cancelled");
    let pages: Vec<_> = (0..5)
        .step_by(2)
        .flat_map(|offset| matches.rows(&history, &placement, offset, 2, 0))
        .map(|row| row.id)
        .collect();
    assert_eq!(
        pages,
        order(SortKey::Class, false),
        "pages tile the listing"
    );
    assert!(matches.rows(&history, &placement, 5, 2, 0).is_empty());
    let second = &matches.rows(&history, &placement, 2, 1, 4)[0];
    assert_eq!(
        (
            second.id.as_str(),
            second.classes.clone(),
            second.first_frame,
            second.last_frame
        ),
        ("id9", ids(&["alpha", "beta"]), 1, 1)
    );
    assert_eq!(second.presence, [[1, 1]]);
}

#[test]
fn a_listing_longer_than_one_sorted_run_is_still_in_order() {
    let names: Vec<String> = (0..20_001).rev().map(|n| format!("id{n}")).collect();
    let entities: Vec<(&str, &[&str])> =
        names.iter().map(|name| (name.as_str(), &[][..])).collect();
    let history = history_of(&[scene_of(0, &entities)], &policy_of(&[]));
    let placement = one_to_one(1);

    let ascending = listed(
        &history,
        &placement,
        &query(&[], Search::default(), SortKey::Id, false),
    );

    let expected: Vec<String> = (0..20_001).map(|n| format!("id{n}")).collect();
    assert_eq!(ascending, expected);
}

#[test]
fn a_cancelled_query_stops() {
    let history = history_of(&scenes(), &policy_of(&[]));
    let placement = one_to_one(3);
    let cancel = CancelToken::new();
    cancel.cancel();

    let run = query(&[], Search::default(), SortKey::Id, false).run(&history, &placement, &cancel);

    assert!(run.is_err());
}

/// Samples 0..=4 hold these entities.
fn appearances() -> History {
    history_of(
        &[
            scene_of(0, &[("a", &[]), ("d", &[])]),
            scene_of(1, &[("a", &[]), ("b", &[])]),
            scene_of(2, &[("a", &[])]),
            scene_of(3, &[("b", &[]), ("d", &[])]),
            scene_of(4, &[("c", &[])]),
        ],
        &policy_of(&[]),
    )
}

/// The frames of `runs` by asking the Overlay Lookup about every frame, as spans of consecutive frames.
fn by_selection(
    keys: &SampleKeys,
    coverage: Coverage,
    frames: usize,
    policy: &MatchPolicy,
    runs: &[[u32; 2]],
) -> Vec<[u32; 2]> {
    let timeline = timeline(frames);
    let mut spans: Vec<[u32; 2]> = Vec::new();
    for frame in 0..frames as u32 {
        let id = timeline.frame(u64::from(frame)).expect("a frame");
        let shown = match select(keys, coverage, id, &timeline, policy) {
            Selection::Sample { index, .. } => runs
                .iter()
                .any(|&[first, last]| (first as usize..=last as usize).contains(&index)),
            Selection::None | Selection::Empty => false,
        };
        match spans.last_mut() {
            Some(last) if shown && last[1] + 1 == frame => last[1] = frame,
            _ if shown => spans.push([frame, frame]),
            _ => {}
        }
    }
    spans
}

#[test]
fn placement_puts_objects_on_exactly_the_frames_the_lookup_shows_their_samples() {
    let history = appearances();
    let times = SampleKeys {
        frames: None,
        times: Some(KeyChunks::new().appended(&[
            MediaTime(0),
            MediaTime(120_000),
            MediaTime(130_000),
            MediaTime(600_000),
            // Past the end of a 30 frame video.
            MediaTime(5_000_000),
        ])),
    };
    let frames = frame_keys(&[2, 3, 4, 10, 40]);
    let policy = |basis, timeout: Option<Option<MediaTime>>| MatchPolicy {
        basis: Some(basis),
        sticky: Sticky {
            enabled: timeout.is_some(),
            timeout: timeout.flatten(),
        },
        ..MatchPolicy::default()
    };
    let (by_frame, by_time) = (
        policy(MatchBasis::Frame, None),
        policy(MatchBasis::Time, None),
    );
    let cases = [
        (&frames, Coverage::Dense, by_frame),
        (&frames, Coverage::Sparse, by_frame),
        (
            &frames,
            Coverage::Sparse,
            policy(MatchBasis::Frame, Some(None)),
        ),
        (
            &frames,
            Coverage::Sparse,
            policy(MatchBasis::Frame, Some(Some(MediaTime(200_000)))),
        ),
        (
            &frames,
            Coverage::Dense,
            policy(MatchBasis::Frame, Some(None)),
        ),
        (&times, Coverage::Sparse, by_time),
        (
            &times,
            Coverage::Sparse,
            MatchPolicy {
                tolerance: Tolerance::Exact,
                ..by_time
            },
        ),
        (
            &times,
            Coverage::Sparse,
            policy(MatchBasis::Time, Some(None)),
        ),
        (
            &times,
            Coverage::Sparse,
            policy(MatchBasis::Time, Some(Some(MediaTime(100_000)))),
        ),
    ];

    for (n, (keys, coverage, policy)) in cases.iter().enumerate() {
        let placement = place(keys, 30, *coverage, policy);
        for entity in 0..history.entities() as u32 {
            let runs = history.runs(entity);
            let spans = by_selection(keys, *coverage, 30, policy, runs);
            assert_eq!(
                placement.spans(runs),
                spans,
                "case {n}, {}",
                history.id(entity)
            );
            assert_eq!(
                placement.bounds(runs),
                spans
                    .first()
                    .zip(spans.last())
                    .map(|(first, last)| (first[0], last[1])),
                "case {n}, {}",
                history.id(entity)
            );
        }
    }
    // c is in the sample past the end of the video: it is shown nowhere, so no list has it.
    let placement = place(&frames, 30, Coverage::Dense, &MatchPolicy::default());
    let listing = listed(
        &history,
        &placement,
        &query(&[], Search::default(), SortKey::Id, false),
    );
    assert_eq!(listing, ids(&["a", "b", "d"]));
}

#[test]
fn presence_marks_stay_within_the_bins_and_cover_the_frames() {
    let frames = 1000;
    let placement = one_to_one(frames);
    // Runs of consecutive samples with irregular gaps between them.
    let (mut seed, mut at) = (7_u32, 0_u32);
    let mut runs = Vec::new();
    while at < frames as u32 - 40 {
        seed = seed.wrapping_mul(1_103_515_245).wrapping_add(12_345);
        let (start, end) = (
            at + 2 + (seed >> 16) % 30,
            at + 2 + (seed >> 16) % 30 + (seed >> 8) % 12,
        );
        runs.push([start, end]);
        at = end;
    }
    let spans = placement.spans(&runs);

    for bins in [0_u16, 1, 3, 10, 64, 999, 2000] {
        let marks = placement.presence(&spans, bins);
        assert!(marks.len() <= usize::from(bins), "{bins} bins: {marks:?}");
        for span in &spans {
            let covered = marks
                .iter()
                .any(|mark| mark[0] <= u64::from(span[0]) && u64::from(span[1]) <= mark[1]);
            assert!(covered || bins == 0, "{bins} bins lose {span:?}");
        }
        let gap = frames as u64 / u64::from(bins.max(1));
        assert!(
            marks.windows(2).all(|pair| pair[1][0] - pair[0][1] > gap),
            "{bins} bins leave a gap shorter than {gap}: {marks:?}"
        );
    }
}
