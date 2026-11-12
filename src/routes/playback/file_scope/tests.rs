//! File scope answers through real overlays: what a page carries, which request is answered and who is refused.

use super::cache::Cache;
use super::*;
use api_types::playback::{EventAskWire, EventQueryWire};
use axum::extract::ws::Message;
use foundation::{MediaTime, Timeline};
use overlays::{LaneStatus, OverlayEvent, OverlayService};
use scene_store::{Op, Row, SortKey, Term};
use std::{path::Path, time::Duration};
use workspace::OverlayRef;

/// A viewer of a MOT file on two lanes, ready, whose lanes have policy revision 1: object 1 is on every one of 90
/// frames and object 2 on the first 30. Returns the data revision.
pub(super) async fn viewer(folder: &Path) -> (ViewerOverlays, u32) {
    let gt = folder.join("gt.txt");
    let rows: String = (1..=90)
        .flat_map(|frame| {
            (1..=2)
                .filter(move |id| *id == 1 || frame <= 30)
                .map(move |id| format!("{frame},{id},10,10,20,20,1,1,1\n"))
        })
        .collect();
    std::fs::write(&gt, rows).unwrap();
    let overlay = OverlayRef {
        label: "GT".into(),
        slot: "GT".into(),
        path: gt,
        handler: "MOT_FILE".into(),
        options: handler_api::Settings::from_iter([
            ("width".to_owned(), 64.into()),
            ("height".to_owned(), 48.into()),
        ]),
        metadata: Default::default(),
    };
    let service = OverlayService::new(scene_store::TrackRegistry::new(
        handlers::Handlers::built_in().decoder_lookup(),
        None,
    ));
    let timeline =
        Timeline::new((0..90).map(|n| MediaTime::from_us(n * 33_333)).collect()).unwrap();
    let mut viewer = service.open_viewer(
        vec![Some(overlay.clone()), Some(overlay)],
        timeline,
        53_687_091,
    );
    let (mut ready, mut revision) = (0_u8, 0);
    while ready != 0b11 {
        let event = tokio::time::timeout(Duration::from_secs(20), viewer.next_event(None))
            .await
            .expect("an event in time")
            .expect("the viewer is open");
        if let OverlayEvent::Status {
            lane,
            status: LaneStatus::Ready(status),
            ..
        } = event
        {
            viewer.set_policy(lane, 1, Some(overlay_sync::MatchPolicy::default()), true);
            ready |= 1 << lane;
            revision = status.data_revision;
        }
    }
    (viewer, revision)
}

fn ask(request: u32, search: Option<(&str, &str)>, offset: u32, limit: u32) -> Ask {
    let groups = search
        .map(|(path, value)| {
            vec![vec![Term {
                path: Some(path.into()),
                op: Op::Eq,
                value: value.into(),
                negated: false,
            }]]
        })
        .unwrap_or_default();
    Ask {
        lane: 0,
        request,
        query: Search { groups },
        classes: vec![u32::MAX],
        sort: Sort {
            key: SortKey::Id,
            descending: false,
        },
        offset,
        limit,
        bins: 4,
        locate: None,
    }
}

async fn next(scopes: &mut FileScopes, own: bool) -> ServerMessage {
    let reply = tokio::time::timeout(Duration::from_secs(20), scopes.next())
        .await
        .expect("an answer in time");
    let outgoing = reply.outgoing(own);
    assert!(
        outgoing.frames.is_none() && outgoing.data.is_none(),
        "never held back for a frame"
    );
    let Message::Text(body) = outgoing.message else {
        panic!("a text message");
    };
    serde_json::from_str(&body).unwrap()
}

fn answered(message: ServerMessage) -> (u32, FileAnswer) {
    let ServerMessage::File {
        request, answer, ..
    } = message
    else {
        panic!("a file answer, got {message:?}");
    };
    (request, answer)
}

fn following() -> FileAnswer {
    FileAnswer::Unavailable {
        unavailable: FileScopeUnavailable::Following,
    }
}

/// The revisions and body of a found answer.
fn body(message: ServerMessage) -> (u32, u32, FileBody) {
    let (_, answer) = answered(message);
    let FileAnswer::Found {
        data_revision,
        policy_revision,
        body,
    } = answer
    else {
        panic!("a found answer, got {answer:?}");
    };
    (data_revision, policy_revision, body)
}

fn rows(message: ServerMessage) -> (u32, u32, u64, u64, Vec<Row>) {
    let (data, policy, body) = body(message);
    let FileBody::Objects {
        total,
        matched,
        rows,
        ..
    } = body
    else {
        panic!("rows, got {body:?}");
    };
    (data, policy, total, matched, rows)
}

#[tokio::test]
async fn a_page_lists_the_objects_the_video_shows_under_the_revisions_the_lane_has_now() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, revision) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);

    scopes.ask(&viewer, true, ask(1, None, 0, 10)).ok().unwrap();
    let (data, policy, total, matched, listed) = rows(next(&mut scopes, true).await);
    assert_eq!((data, policy, total, matched), (revision, 1, 2, 2));
    let ids: Vec<_> = listed.iter().map(|row| row.id.as_str()).collect();
    assert_eq!(ids, ["1", "2"]);
    let (long, short) = (&listed[0], &listed[1]);
    assert_eq!(long.last_frame - long.first_frame, 89);
    assert_eq!(
        short.last_frame - short.first_frame,
        29,
        "object 2 is on 30 frames"
    );
    assert_eq!(short.first_frame, long.first_frame);
    assert_eq!(short.presence, [[short.first_frame, short.last_frame]]);

    scopes
        .ask(&viewer, true, ask(2, Some(("id", "2")), 0, 10))
        .ok()
        .unwrap();
    let (_, _, total, matched, listed) = rows(next(&mut scopes, true).await);
    assert_eq!(
        (total, matched, listed.len()),
        (2, 1, 1),
        "the search narrows, the total does not"
    );

    scopes.ask(&viewer, true, ask(3, None, 1, 1)).ok().unwrap();
    let (_, _, _, matched, listed) = rows(next(&mut scopes, true).await);
    assert_eq!(
        (matched, listed[0].id.as_str()),
        (2, "2"),
        "a page of the listing"
    );

    viewer.set_policy(0, 2, Some(overlay_sync::MatchPolicy::default()), true);
    scopes.ask(&viewer, true, ask(4, None, 0, 10)).ok().unwrap();
    assert_eq!(
        rows(next(&mut scopes, true).await).1,
        2,
        "the lane's policy revision now"
    );
}

#[tokio::test]
async fn a_follower_is_refused_and_only_the_newest_request_of_a_lane_is_answered() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, _) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);

    scopes
        .ask(&viewer, false, ask(1, None, 0, 10))
        .ok()
        .unwrap();
    assert_eq!(answered(next(&mut scopes, false).await), (1, following()));

    for request in [2, 3] {
        scopes
            .ask(&viewer, true, ask(request, None, 0, 10))
            .ok()
            .unwrap();
    }
    assert_eq!(answered(next(&mut scopes, true).await).0, 3);
    assert!(
        tokio::time::timeout(Duration::from_millis(300), scopes.next())
            .await
            .is_err(),
        "the replaced request is never answered"
    );

    // A viewer that became a follower while the query ran is not sent the rows.
    scopes.ask(&viewer, true, ask(4, None, 0, 10)).ok().unwrap();
    assert_eq!(answered(next(&mut scopes, false).await), (4, following()));

    let outside = Ask {
        lane: 5,
        ..ask(5, None, 0, 10)
    };
    assert!(matches!(
        scopes.ask(&viewer, true, outside),
        Err(End::Failed(_))
    ));
}

#[tokio::test]
async fn a_released_lane_is_not_answered_and_keeps_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, _) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);

    scopes.ask(&viewer, true, ask(1, None, 0, 10)).ok().unwrap();
    let spans = appearances::Ask {
        lane: 0,
        request: 1,
        entity: "2".into(),
    };
    scopes.ask_appearances(&viewer, true, spans).ok().unwrap();
    scopes.release(&viewer, 0).ok().unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(500), scopes.next())
            .await
            .is_err(),
        "the queries that were running are never answered"
    );
    assert!(scopes.cache.held().is_empty(), "and keeps nothing");

    scopes.ask(&viewer, true, ask(2, None, 0, 10)).ok().unwrap();
    rows(next(&mut scopes, true).await);
    assert_eq!(scopes.cache.held().len(), 1);
    scopes.release(&viewer, 0).ok().unwrap();
    assert!(scopes.cache.held().is_empty(), "the answered lane is freed");

    assert!(matches!(scopes.release(&viewer, 5), Err(End::Failed(_))));
}

#[tokio::test]
async fn the_lanes_keep_no_more_than_the_budget_and_the_longest_unused_goes_first() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, _) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);
    let other = |request| Ask {
        lane: 1,
        ..ask(request, None, 0, 10)
    };

    scopes.ask(&viewer, true, ask(1, None, 0, 10)).ok().unwrap();
    rows(next(&mut scopes, true).await);
    let one = scopes.cache.held()[0].1;
    scopes.cache = Arc::new(Cache::new(one + one / 2));

    // Over a budget that fits one lane, the lane asked last is kept and so is its paging.
    for (request, ask) in [(2, ask(2, None, 0, 10)), (3, other(3)), (4, other(4))] {
        scopes.ask(&viewer, true, ask).ok().unwrap();
        rows(next(&mut scopes, true).await);
        let held = scopes.cache.held();
        assert!(held.iter().map(|(_, bytes)| bytes).sum::<usize>() <= one + one / 2);
        assert_eq!(
            held.last().map(|held| held.0),
            Some(if request == 2 { 0 } else { 1 })
        );
    }
    assert_eq!(
        scopes.cache.held().len(),
        1,
        "the lane used longest ago was dropped"
    );
}

#[tokio::test]
async fn the_spans_of_an_object_come_beside_the_list() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, revision) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);
    let spans = |entity: &str| appearances::Ask {
        lane: 0,
        request: 5,
        entity: entity.into(),
    };
    let found = |message: ServerMessage| {
        let (data, policy, body) = body(message);
        let FileBody::Appearances {
            entity,
            spans,
            truncated,
            ..
        } = body
        else {
            panic!("spans, got {body:?}");
        };
        (entity, data, policy, spans, truncated)
    };

    scopes.ask(&viewer, true, ask(1, None, 0, 10)).ok().unwrap();
    scopes
        .ask_appearances(&viewer, true, spans("2"))
        .ok()
        .unwrap();
    let mut answers = [next(&mut scopes, true).await, next(&mut scopes, true).await];
    answers.sort_by_key(|message| answered(message.clone()).0);
    let [rows_answer, spans_answer] = answers;
    assert_eq!(rows(rows_answer).4.len(), 2, "the list is still answered");
    assert_eq!(
        found(spans_answer),
        ("2".into(), revision, 1, vec![[0, 29]], false)
    );

    scopes
        .ask_appearances(&viewer, true, spans("nobody"))
        .ok()
        .unwrap();
    assert_eq!(
        found(next(&mut scopes, true).await).3,
        Vec::<[u64; 2]>::new()
    );

    // Of two requests for a lane in a row only the newer is answered, even though the older one ran.
    for request in [6, 7] {
        let ask = appearances::Ask {
            request,
            ..spans("2")
        };
        scopes.ask_appearances(&viewer, true, ask).ok().unwrap();
    }
    assert_eq!(answered(next(&mut scopes, true).await).0, 7);
    assert!(
        tokio::time::timeout(Duration::from_millis(300), scopes.next())
            .await
            .is_err(),
        "the replaced request is never answered"
    );

    // Callers that need a lane's history and placement together read them once.
    scopes.release(&viewer, 0).ok().unwrap();
    scopes.cache.open(0);
    let reads = std::sync::atomic::AtomicU32::new(0);
    std::thread::scope(|threads| {
        for _ in 0..2 {
            let input = viewer.file_scope(0).ok().unwrap();
            let (cache, reads) = (&scopes.cache, &reads);
            threads.spawn(move || {
                cache
                    .prepared(0, &input, || {
                        reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                        std::thread::sleep(Duration::from_millis(200));
                        read(&input, &CancelToken::new(), Audience::HOST)
                    })
                    .ok()
                    .unwrap();
            });
        }
    });
    assert_eq!(reads.into_inner(), 1);
}

#[tokio::test]
async fn event_answers_carry_their_revisions_a_follower_is_refused_and_a_newer_request_replaces_the_older()
 {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, revision) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);
    let list = |request| {
        EventsAsk::List(EventQueryWire {
            lane: 0,
            request,
            query: Search::default(),
            offset: 0,
            limit: 10,
            locate: None,
            frame: Some(3),
        })
    };
    let one = |request| {
        EventsAsk::Detail(EventAskWire {
            lane: 0,
            request,
            event: 0,
            bins: 4,
        })
    };

    // A MOT file has no events: the answers are empty but made under the lane's revisions.
    scopes.ask_events(&viewer, true, list(1)).ok().unwrap();
    scopes.ask_events(&viewer, true, one(2)).ok().unwrap();
    let mut answers = [next(&mut scopes, true).await, next(&mut scopes, true).await];
    answers.sort_by_key(|message| answered(message.clone()).0);
    let [rows_answer, event_answer] = answers;
    let (data, policy, listed) = body(rows_answer);
    let FileBody::Events {
        total,
        matched,
        frame_row,
        ..
    } = listed
    else {
        panic!("event rows, got {listed:?}");
    };
    assert_eq!(
        (data, policy, total, matched, frame_row),
        (revision, 1, 0, 0, Some(0))
    );
    let (data, policy, event) = body(event_answer);
    assert!(matches!(
        event,
        FileBody::Event {
            event: 0,
            detail: None
        }
    ));
    assert_eq!((data, policy), (revision, 1));

    // Of two requests in a row only the newer is answered.
    for request in [2, 3] {
        scopes
            .ask_events(&viewer, true, list(request))
            .ok()
            .unwrap();
    }
    assert_eq!(answered(next(&mut scopes, true).await).0, 3);
    assert!(
        tokio::time::timeout(Duration::from_millis(300), scopes.next())
            .await
            .is_err(),
        "the replaced request is never answered"
    );

    // A follower is refused when it asks, and when it became one while the request ran.
    scopes.ask_events(&viewer, false, one(4)).ok().unwrap();
    scopes.ask_events(&viewer, true, list(5)).ok().unwrap();
    for _ in 0..2 {
        assert_eq!(answered(next(&mut scopes, false).await).1, following());
    }
}

#[tokio::test]
async fn an_answer_changes_only_its_own_listing_in_the_entry_of_its_revisions() {
    let folder = tempfile::tempdir().unwrap();
    let (viewer, revision) = viewer(folder.path()).await;
    let mut scopes = FileScopes::new(Audience::HOST);
    scopes.ask(&viewer, true, ask(1, None, 0, 10)).ok().unwrap();
    rows(next(&mut scopes, true).await);
    let kept = scopes.cache.held()[0].1;

    scopes
        .cache
        .update(0, (revision, 2), |kept| kept.listing = None);
    assert_eq!(
        scopes.cache.held()[0].1,
        kept,
        "other revisions are not its entry"
    );
    scopes
        .cache
        .update(0, (revision, 1), |kept| kept.event_listing = None);
    assert_eq!(
        scopes.cache.held()[0].1,
        kept,
        "the objects listing is not the events'"
    );
    scopes
        .cache
        .update(0, (revision, 1), |kept| kept.listing = None);
    assert!(scopes.cache.held()[0].1 < kept);
}
