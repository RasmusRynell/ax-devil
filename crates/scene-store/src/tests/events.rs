//! Listing the events of a file on a video: where each sits, what a search keeps, and what one event shows.

use foundation::{MediaTime, Timeline};
use overlay_sync::{MatchBasis, MatchPolicy};
use scene::{Event, Operation, OperationKind};

use super::{
    history::{history_of, policy_of, scene_of},
    *,
};
use crate::{EventPlacement, EventQuery, EventView, History, Op, Placement, Search, Term};

fn id(name: &str) -> EntityId {
    EntityId::new(name).expect("an id")
}

/// An event on sample `sample`.
fn event(sample: u64, operation: Operation) -> Event {
    Event::new(TimeSlice::at(SceneTime::Frame(sample)), operation)
}

/// Five samples. `a` (a car) is in 0 and 1, `b` (a bus) in 0 to 3. Events: 0 deletes `a`; 1 renames `a` to `z`; 2 merges
/// `a` and `b` into `c`; 3 splits `b`; 4 deletes `b`; 5 deletes `c`. Events 1 and 2 are on one sample.
fn history() -> History {
    let at = |sample: u64, entities: &[(&str, &[&str])], events: Vec<Event>| {
        scene_of(sample, entities)
            .with_events(events)
            .expect("one clock")
    };
    let both: &[(&str, &[&str])] = &[("a", &["car"]), ("b", &["bus"])];
    history_of(
        &[
            at(
                0,
                both,
                vec![event(0, Operation::Delete { entity: id("a") })],
            ),
            at(
                1,
                both,
                vec![
                    event(
                        1,
                        Operation::Rename {
                            from: id("a"),
                            to: id("z"),
                        },
                    ),
                    event(
                        1,
                        Operation::Merge {
                            sources: vec![id("a"), id("b")],
                            target: id("c"),
                        },
                    ),
                ],
            ),
            at(
                2,
                &[("b", &["bus"])],
                vec![event(
                    2,
                    Operation::Split {
                        source: id("b"),
                        targets: vec![id("b1"), id("b2")],
                    },
                )],
            ),
            at(
                3,
                &[("b", &["bus"])],
                vec![event(3, Operation::Delete { entity: id("b") })],
            ),
            at(
                4,
                &[],
                vec![event(4, Operation::Delete { entity: id("c") })],
            ),
        ],
        &policy_of(&[]),
    )
}

/// A 5 frame video at 40 ms; the file's samples are at these times and frames. Sample 0 is before the video and
/// sample 4 beyond it, by time and by frame.
fn keys() -> SampleKeys {
    SampleKeys {
        frames: Some(in_order(&[0, 1, 3, 4, 40])),
        times: Some(
            [-5_000, 10_000, 40_000, 70_000, 5_000_000]
                .map(MediaTime)
                .into_iter()
                .collect(),
        ),
    }
}

fn timeline() -> Timeline {
    Timeline::new((0..5).map(|n| MediaTime(n * 40_000)).collect()).expect("a timeline")
}

/// The events of [`history`] placed by `basis`, with objects placed by frame.
struct Fixture {
    history: History,
    events: EventPlacement,
    placement: Placement,
}

fn fixture(basis: MatchBasis) -> Fixture {
    let (history, keys, cancel) = (history(), keys(), CancelToken::new());
    let events =
        EventPlacement::new(&history, &keys, &timeline(), basis, &cancel).expect("not cancelled");
    let policy = MatchPolicy {
        basis: Some(MatchBasis::Frame),
        ..MatchPolicy::default()
    };
    let placement = Placement::new(&keys, Coverage::Sparse, &timeline(), &policy, &cancel)
        .expect("not cancelled");
    Fixture {
        history,
        events,
        placement,
    }
}

impl Fixture {
    fn view(&self) -> EventView<'_> {
        EventView {
            history: &self.history,
            events: &self.events,
            placement: &self.placement,
        }
    }

    fn run(&self, search: Search) -> crate::EventMatches {
        EventQuery { search }
            .run(&self.history, &self.events, &CancelToken::new())
            .expect("not cancelled")
    }

    /// The `(event, frame)` of every row the search lists.
    fn listed(&self, search: Search) -> Vec<(u32, u64)> {
        let matches = self.run(search);
        let rows = matches.rows(self.view(), 0, usize::MAX);
        assert_eq!(rows.len() as u64, matches.matched());
        rows.iter().map(|row| (row.event, row.frame)).collect()
    }
}

fn search(path: Option<&str>, value: &str, negated: bool) -> Search {
    Search {
        groups: vec![vec![Term {
            path: path.map(str::to_string),
            op: Op::Eq,
            value: value.to_string(),
            negated,
        }]],
    }
}

#[test]
fn an_event_sits_on_the_first_frame_at_or_after_its_time_or_on_its_frame_and_never_outside_the_video()
 {
    // Time: 10 ms is in the gap before frame 1, 40 ms is frame 1 itself and 70 ms is before frame 2. The events of the
    // sample before the first frame and of the sample after the last are left out.
    assert_eq!(
        fixture(MatchBasis::Time).listed(Search::default()),
        [(1, 1), (2, 1), (3, 1), (4, 2)]
    );
    // Frame: each sample's own frame number, whatever the times say; frame 40 is not in the video.
    let by_frame = fixture(MatchBasis::Frame);
    assert_eq!(
        by_frame.listed(Search::default()),
        [(0, 0), (1, 1), (2, 1), (3, 3), (4, 4)]
    );
    // A basis the file has no keys for places nothing.
    let nothing = EventPlacement::new(
        &by_frame.history,
        &SampleKeys {
            frames: None,
            times: None,
        },
        &timeline(),
        MatchBasis::Frame,
        &CancelToken::new(),
    )
    .expect("not cancelled");
    let run = EventQuery::default().run(&by_frame.history, &nothing, &CancelToken::new());
    assert_eq!(run.expect("not cancelled").total(), 0);
}

#[test]
fn a_search_reads_kind_object_and_label_and_the_listing_pages_and_locates_the_line() {
    let fixture = fixture(MatchBasis::Frame);
    let events = |search: Search| -> Vec<u32> {
        fixture.listed(search).into_iter().map(|(e, _)| e).collect()
    };

    assert_eq!(events(search(Some("kind"), "MERGE", false)), [2]);
    assert_eq!(events(search(Some("object"), "b", false)), [2, 3, 4]);
    assert_eq!(events(search(Some("object"), "b", true)), [0, 1]);
    assert_eq!(
        events(search(None, "rename a", false)),
        [1],
        "free text looks in the label"
    );
    assert!(events(search(Some("class"), "car", false)).is_empty());

    let all = fixture.run(Search::default());
    assert_eq!((all.total(), all.matched()), (5, 5));
    let paged: Vec<u32> = all
        .rows(fixture.view(), 1, 2)
        .iter()
        .map(|row| row.event)
        .collect();
    assert_eq!(paged, [1, 2]);
    assert_eq!(all.row_of(3), Some(3));

    let narrowed = fixture.run(search(Some("object"), "b", false));
    assert_eq!((narrowed.total(), narrowed.matched()), (5, 3));
    assert_eq!(narrowed.row_of(0), None);
    // The line goes before the first listed event after the frame.
    let line = |frame| narrowed.frame_row(&fixture.events, frame);
    assert_eq!(
        (line(0), line(1), line(2), line(3), line(9)),
        (0, 1, 1, 2, 3)
    );
}

#[test]
fn an_event_shows_each_object_once_where_it_is_seen_and_the_other_events_of_its_frame() {
    let fixture = fixture(MatchBasis::Frame);
    let view = fixture.view();

    let rename = view.detail(1, 4).expect("the video shows it");
    assert_eq!(
        (rename.row.kind, rename.row.label.as_str()),
        (OperationKind::Rename, "Rename a → z")
    );
    let named: Vec<_> = rename.objects.iter().map(|o| o.id.as_str()).collect();
    assert_eq!(named, ["a", "z"]);
    assert_eq!(rename.objects[0].classes, ["car"]);
    // Frame 2 has no sample of its own and keeps showing sample 1 under the default tolerance.
    assert_eq!(rename.objects[0].seen, Some([0, 2]));
    assert_eq!(rename.objects[0].presence, [[0, 2]]);
    assert_eq!(
        (&rename.objects[1].classes[..], rename.objects[1].seen),
        (&[][..], None),
        "an id only an event names is seen nowhere"
    );
    let moment: Vec<u32> = rename.same_moment.iter().map(|row| row.event).collect();
    assert_eq!(moment, [2], "not the event itself");

    // The chip of an object the video shows takes its class; one it never shows takes the event's kind.
    let chips: Vec<_> = rename
        .row
        .objects
        .iter()
        .map(|o| o.class.as_deref())
        .collect();
    assert_eq!(chips, [Some("car"), None]);

    assert!(view.detail(5, 4).is_none(), "the video does not show it");
    let on_b: Vec<u32> = view.object_events("b").iter().map(|r| r.event).collect();
    assert_eq!(on_b, [2, 3, 4]);
    assert!(view.object_events("nobody").is_empty());
}
