//! Tickets, demand, stale jobs, the View lane and the session of one.

use crate::{Rig, authority, stream};
use playback::{Authority, ClockIntent, Priority};
use sessions::{
    CursorOptions,
    session::{
        Access, Command, Completion, Control, Refusal, Reply, Sidebar, SidebarView, View, ViewLane,
        ViewerView,
    },
};
use std::time::Duration;
use tokio::time::{Instant, advance};
use workspace_store::{DocCommand, Resolution};

#[tokio::test(start_paused = true)]
async fn a_ticket_opens_one_stream_once_and_only_for_the_seat_and_key_it_was_issued_for() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let Reply::Ticket(ticket) = watcher
        .command(Command::RequestTicket(key.clone()))
        .unwrap()
    else {
        panic!("a ticket");
    };

    let lease = rig.service.redeem(&ticket).unwrap();
    assert!(matches!(authority(&lease), Authority::Follow { .. }));
    assert_eq!(
        rig.service.redeem(&ticket).err(),
        Some(Refusal::BadTicket),
        "single use"
    );

    let Reply::Ticket(late) = watcher
        .command(Command::RequestTicket(key.clone()))
        .unwrap()
    else {
        panic!("a ticket");
    };
    advance(Duration::from_secs(11)).await;
    assert_eq!(
        rig.service.redeem(&late).err(),
        Some(Refusal::BadTicket),
        "expired"
    );

    let Reply::Ticket(bound) = watcher
        .command(Command::RequestTicket(key.clone()))
        .unwrap()
    else {
        panic!("a ticket");
    };
    let newer = rig.service.resume(watcher.resume_token()).unwrap();
    assert_eq!(
        rig.service.redeem(&bound).err(),
        Some(Refusal::BadTicket),
        "bound to the lease it was asked under"
    );
    let Reply::Ticket(for_key) = newer.command(Command::RequestTicket(key.clone())).unwrap() else {
        panic!("a ticket");
    };
    let item = *owner.state().doc.workspace.open.iter().next().unwrap();
    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();
    owner
        .command(Command::Content(DocCommand::OpenViewer(item)))
        .unwrap();
    rig.jobs.finish_all();
    assert_eq!(
        rig.service.redeem(&for_key).err(),
        Some(Refusal::BadTicket),
        "bound to the generation in its key"
    );
}

#[tokio::test(start_paused = true)]
async fn a_seat_holds_eight_streams_and_a_session_sixty_four() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let watchers: Vec<_> = (0..8).map(|_| rig.join(&owner)).collect();
    let mut leases = Vec::new();

    for watcher in &watchers {
        for _ in 0..8 {
            leases.push(stream(&rig.service, watcher, &key).unwrap());
        }
        assert_eq!(
            stream(&rig.service, watcher, &key).err(),
            Some(Refusal::TooManyStreams)
        );
    }
    assert_eq!(
        stream(&rig.service, &owner, &key).err(),
        Some(Refusal::TooManyStreams),
        "the session is full"
    );

    leases.pop();
    assert!(
        stream(&rig.service, &owner, &key).is_ok(),
        "a released stream frees its place"
    );
}

#[tokio::test(start_paused = true)]
async fn a_following_seat_gets_the_presenters_clock_and_the_owner_its_own_frames() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (item, key) = rig.open_video(&owner, "a.mp4");
    let following = stream(&rig.service, &watcher, &key).unwrap();
    let own = stream(&rig.service, &owner, &key).unwrap();
    assert_eq!(
        authority(&following),
        Authority::Follow {
            intent: ClockIntent {
                frame: 0,
                playing: false,
                speed_centi: 100,
                at_us: 0
            }
        },
        "paused at the start until the presenter says otherwise"
    );
    assert_eq!(
        authority(&own),
        Authority::Own {
            priority: Priority::High
        }
    );

    let intent = ClockIntent {
        frame: 120,
        playing: true,
        speed_centi: 200,
        at_us: 0,
    };
    let clock = ViewerView {
        clock: Some(intent),
    };
    let view = View {
        viewers: [(item, clock)].into(),
        ..View::default()
    };
    owner.view(view, 1).unwrap();

    assert_eq!(authority(&following), Authority::Follow { intent });
}

#[tokio::test(start_paused = true)]
async fn a_presenter_view_change_keeps_the_stored_clock_and_a_new_intent_is_stamped_within_two_seconds()
 {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (item, key) = rig.open_video(&owner, "a.mp4");
    let following = stream(&rig.service, &watcher, &key).unwrap();
    let playing = ClockIntent {
        frame: 120,
        playing: true,
        speed_centi: 100,
        at_us: rig.service.now_us(),
    };
    let view = |intent: ClockIntent, open: bool| View {
        sidebar: Sidebar {
            view: SidebarView::Workspace,
            open,
        },
        viewers: [(
            item,
            ViewerView {
                clock: Some(intent),
            },
        )]
        .into(),
        ..View::default()
    };
    owner.view(view(playing, true), 1).unwrap();
    advance(Duration::from_secs(10)).await;

    // The window re-sends the same intent with every view change: the sidebar toggle must not restamp it.
    owner.view(view(playing, false), 2).unwrap();
    assert_eq!(authority(&following), Authority::Follow { intent: playing });

    // A new intent may not claim a time from the future or from long ago.
    let now = rig.service.now_us();
    let stamped = |seq: u64, at_us: i64| {
        let seek = ClockIntent {
            frame: 300,
            at_us,
            ..playing
        };
        owner.view(view(seek, false), seq).unwrap();
        let Authority::Follow { intent } = authority(&following) else {
            panic!("a following seat follows");
        };
        intent.at_us
    };
    assert_eq!(stamped(3, now + 3_600_000_000), now);
    assert_eq!(stamped(4, 0), now - 2_000_000);
}

#[tokio::test(start_paused = true)]
async fn a_result_that_arrives_late_or_for_a_session_that_is_gone_is_dropped() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    let item = rig.video("a.mp4");
    let id = item.id;
    let add = |item: &workspace::WorkspaceItem| {
        Command::Content(DocCommand::AddItems {
            items: vec![item.clone()],
            open: true,
        })
    };
    owner.command(add(&item)).unwrap();
    owner.command(add(&item)).unwrap();
    let mut jobs = rig.jobs.take();
    let (second, second_done) = jobs.pop().unwrap();
    let (first, first_done) = jobs.pop().unwrap();
    assert!(
        first.cancel.is_cancelled(),
        "resolving again cancels the older attempt"
    );
    assert!(!second.cancel.is_cancelled());

    let resolve = |spawn: &sessions::session::Spawn| {
        let sessions::session::Work::Resolve(job) = &spawn.work;
        Completion::Resolved(workspace_store::resolve(job))
    };
    first_done.complete(resolve(&first));
    let workspace = &owner.state().doc.workspace;
    assert!(matches!(
        workspace.resolutions[&id],
        Resolution::Pending { .. }
    ));

    second_done.complete(resolve(&second));
    assert!(matches!(
        owner.state().doc.workspace.resolutions[&id],
        Resolution::Resolved { .. }
    ));

    owner.command(add(&rig.video("b.mp4"))).unwrap();
    let (late, late_done) = rig.jobs.take().pop().unwrap();
    owner.leave();
    assert!(
        late.cancel.is_cancelled(),
        "the session ending cancels its jobs"
    );
    late_done.complete(resolve(&late));
}

#[test]
fn the_view_lane_keeps_the_latest_view_and_applies_at_most_sixty_a_second() {
    let shows = |sidebar: SidebarView| View {
        sidebar: Sidebar {
            view: sidebar,
            open: true,
        },
        ..View::default()
    };
    let start = Instant::now();
    let mut lane = ViewLane::default();

    lane.offer(1, shows(SidebarView::Live));
    lane.offer(3, shows(SidebarView::Offline));
    lane.offer(2, shows(SidebarView::Plugins));
    let (seq, view) = lane.take(start).unwrap();
    assert_eq!(
        (seq, view),
        (3, shows(SidebarView::Offline)),
        "the latest wins; a late arrival is overwritten"
    );

    lane.offer(4, shows(SidebarView::Sessions));
    assert!(lane.take(start + Duration::from_millis(5)).is_none());
    assert!(lane.take(start + Duration::from_millis(17)).is_some());
}

#[tokio::test(start_paused = true)]
async fn a_session_of_one_works_without_sharing() {
    let rig = Rig::new();
    let owner = rig.service.create(true, CursorOptions::default()).unwrap();
    assert!(owner.state().sharing.is_none());
    assert!(
        rig.service.list().is_empty(),
        "a private session is not listed"
    );
    assert!(matches!(
        rig.service.watch(owner.session(), CursorOptions::default()),
        Err(Refusal::NotSharing)
    ));

    let (_, key) = rig.open_video(&owner, "a.mp4");
    let lease = stream(&rig.service, &owner, &key).unwrap();
    assert_eq!(
        authority(&lease),
        Authority::Own {
            priority: Priority::High
        }
    );
    assert!(owner.command(Command::Follow).is_ok());
    owner.view(View::default(), 1).unwrap();
    assert_eq!(
        owner.command(Command::Control(Control::Stop)).err(),
        Some(Refusal::NotSharing)
    );
}
