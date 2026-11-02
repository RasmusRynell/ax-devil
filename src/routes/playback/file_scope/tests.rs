//! File scope answers through real overlays: what a page carries, which request is answered and who is refused.

use super::cache::Cache;
use super::*;
use api_types::playback::ServerMessage;
use axum::extract::ws::Message;
use foundation::{MediaTime, Timeline};
use overlays::{LaneStatus, OverlayEvent, OverlayService};
use scene_store::{Op, Row, SortKey, Term};
use std::{path::Path, time::Duration};
use workspace::OverlayRef;

/// A viewer of a MOT file on two lanes, ready, whose lanes have policy revision 1: object 1 is on every one of 90
/// frames and object 2 on the first 30. Returns the data revision.
async fn viewer(folder: &Path) -> (ViewerOverlays, u32) {
    let gt = folder.join("gt.txt");
    let rows: String = (1..=90)
        .flat_map(|frame| {
            (1..=2)
                .filter(move |id| *id == 1 || frame <= 30)
                .map(move |id| format!("{frame},{id},10,10,20,20,1,1,1\n"))
        })
        .collect();
    std::fs::write(&gt, rows).unwrap();
    std::fs::write(
        folder.join("seqinfo.ini"),
        "[Sequence]\nframeRate=30\nseqLength=90\nimWidth=64\nimHeight=48\n",
    )
    .unwrap();
    let overlay = OverlayRef {
        label: "GT".into(),
        path: gt,
        handler: "MOT_FILE".into(),
        options: Default::default(),
    };
    let service = OverlayService::new(scene_store::TrackRegistry::new(
        handlers::Handlers::built_in(),
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

fn rows(message: ServerMessage) -> (u32, u32, u64, u64, Vec<Row>) {
    let ServerMessage::FileRows {
        data_revision,
        policy_revision,
        total,
        matched,
        rows,
        ..
    } = message
    else {
        panic!("rows, got {message:?}");
    };
    (data_revision, policy_revision, total, matched, rows)
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
    assert!(matches!(
        next(&mut scopes, false).await,
        ServerMessage::FileScope {
            request: 1,
            unavailable: FileScopeUnavailable::Following,
            ..
        }
    ));

    for request in [2, 3] {
        scopes
            .ask(&viewer, true, ask(request, None, 0, 10))
            .ok()
            .unwrap();
    }
    let message = next(&mut scopes, true).await;
    assert!(matches!(
        message,
        ServerMessage::FileRows { request: 3, .. }
    ));
    assert!(
        tokio::time::timeout(Duration::from_millis(300), scopes.next())
            .await
            .is_err(),
        "the replaced request is never answered"
    );

    // A viewer that became a follower while the query ran is not sent the rows.
    scopes.ask(&viewer, true, ask(4, None, 0, 10)).ok().unwrap();
    assert!(matches!(
        next(&mut scopes, false).await,
        ServerMessage::FileScope {
            request: 4,
            unavailable: FileScopeUnavailable::Following,
            ..
        }
    ));

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
    scopes.release(&viewer, 0).ok().unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(500), scopes.next())
            .await
            .is_err(),
        "the query that was running is never answered"
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
