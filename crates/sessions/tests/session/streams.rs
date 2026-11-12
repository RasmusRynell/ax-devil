//! Stream authority, demand, stale jobs, the View lane and the session of one.

use crate::{Rig, authority, entry_key, looking, pane, playlist_in, stream};
use playback::{Authority, ClockIntent, Priority};
use sessions::{
    CursorOptions, SeatRole,
    session::{
        Access, Axis, Command, ContentRef, Control, Layout, Refusal, Sidebar, SidebarView, View,
        ViewLane, ViewerId, ViewerView,
    },
};
use std::time::Duration;
use tokio::time::{Instant, advance};
use workspace::store::{DocCommand, Resolution};
use workspace::{ContentId, ItemId, LiveOverlay, LiveRecipe, Protocol, Recipe, Secret, Source};

#[tokio::test(start_paused = true)]
async fn a_following_seat_gets_the_clock_and_entry_of_the_presenter_pane_it_streams() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (item, key) = rig.open(&owner, playlist_in(rig.dir.path(), &["a", "b"]));
    let second = ContentRef {
        viewer: ViewerId(2),
        ..entry_key(&owner.state(), item, 1)
    };
    let following = stream(&rig.service, &guest, &key).unwrap();
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
    assert_eq!(
        stream(&rig.service, &guest, &second).err(),
        Some(Refusal::ContentUnavailable),
        "the presenter has no second pane yet"
    );

    // Two panes show the one item: the first its first entry at frame 120, the second its second at frame 7.
    let at = |frame, playing| ClockIntent {
        frame,
        playing,
        speed_centi: 200,
        at_us: 0,
    };
    let (first_clock, second_clock) = (at(120, true), at(7, false));
    let view = View {
        active: Some(ViewerId(1)),
        viewers: [
            (
                ViewerId(1),
                ViewerView {
                    clock: Some(first_clock),
                    ..looking(item)
                },
            ),
            (
                ViewerId(2),
                ViewerView {
                    clock: Some(second_clock),
                    entry: Some(second.entry.clone()),
                    ..looking(item)
                },
            ),
        ]
        .into(),
        layout: Some(Layout::Split {
            axis: Axis::Row,
            share: 5000,
            first: pane(1),
            second: pane(2),
        }),
        ..View::default()
    };
    owner.view(view, 2).unwrap();

    assert_eq!(
        authority(&following),
        Authority::Follow {
            intent: first_clock
        }
    );
    let beside = stream(&rig.service, &guest, &second).unwrap();
    assert_eq!(
        authority(&beside),
        Authority::Follow {
            intent: second_clock
        }
    );
    let crossed = ContentRef {
        entry: key.entry.clone(),
        ..second
    };
    assert_eq!(
        stream(&rig.service, &guest, &crossed).err(),
        Some(Refusal::ContentUnavailable),
        "a pane's stream is its own entry, not the item's other pane's"
    );
}

#[tokio::test(start_paused = true)]
async fn a_presenter_view_change_keeps_the_stored_clock_and_a_new_intent_is_stamped_within_two_seconds()
 {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (item, key) = rig.open_video(&owner, "a.mp4");
    let following = stream(&rig.service, &guest, &key).unwrap();
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
            ViewerId(1),
            ViewerView {
                clock: Some(intent),
                ..looking(item)
            },
        )]
        .into(),
        layout: Some(*pane(1)),
        ..View::default()
    };
    owner.view(view(playing, true), 2).unwrap();
    advance(Duration::from_secs(10)).await;

    // The window re-sends the same intent with every view change: the sidebar toggle must not restamp it.
    owner.view(view(playing, false), 3).unwrap();
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
    assert_eq!(stamped(4, now + 3_600_000_000), now);
    assert_eq!(stamped(5, 0), now - 2_000_000);
}

#[tokio::test(start_paused = true)]
async fn a_result_that_arrives_late_or_for_a_session_that_is_gone_is_dropped() {
    let rig = Rig::new();
    let owner = rig
        .service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
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
        crate::completion(job)
    };
    assert!(first_done.complete(resolve(&first)).is_ok());
    let workspace = &owner.state().doc.workspace;
    assert!(matches!(
        workspace.resolutions[&id],
        Resolution::Pending { .. }
    ));

    assert!(second_done.complete(resolve(&second)).is_ok());
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
    assert!(late_done.complete(resolve(&late)).is_ok());
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
    let owner = rig
        .service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    assert!(owner.state().sharing.is_none());
    assert!(rig.service.list(true)[0].link.is_none(), "it has no link");
    assert_eq!(rig.service.list(true)[0].seat, SeatRole::Owner);
    assert!(
        rig.service.list(false).is_empty(),
        "an Owner without Full access has no seat to attach to, so the session is not listed"
    );
    assert!(
        matches!(
            rig.service
                .attach(owner.session(), false, CursorOptions::default()),
            Err(Refusal::NotSharing)
        ),
        "an Owner without Full access is a Guest of a session that expands, and there is no room for one"
    );

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

/// A live video has no entry: its stream is named by the Content's id twice, a seat that follows only watches, and
/// closing the viewer ends the stream (Stop).
#[tokio::test(start_paused = true)]
async fn a_live_stream_is_named_by_its_content_and_stops_when_its_viewer_closes() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let id = ItemId::generate();
    let recipe = LiveRecipe {
        host: "camera.local".into(),
        username: String::new(),
        password: Secret::default(),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: workspace::RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let item = workspace::WorkspaceItem {
        id,
        label: String::new(),
        recipe: Recipe::new(Source::Live(recipe)),
    };
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![item],
            open: true,
        }))
        .unwrap();
    rig.jobs.finish_all();
    let Resolution::Resolved { generation, .. } = owner.state().doc.workspace.resolutions[&id]
    else {
        panic!("the live stream resolves");
    };
    let key = ContentRef {
        content: ContentId::of_item(&id),
        generation,
        entry: ContentId::of_item(&id),
        viewer: ViewerId(1),
    };

    let own = stream(&rig.service, &owner, &key).unwrap();
    let watching = stream(&rig.service, &guest, &key).unwrap();
    assert!(own.entry().is_none() && watching.entry().is_none());
    assert!(matches!(authority(&own), Authority::Own { .. }));
    assert!(matches!(authority(&watching), Authority::Follow { .. }));
    let other = ContentRef {
        entry: ContentId::of_entry(&id, "P", "x"),
        ..key.clone()
    };
    assert_eq!(
        stream(&rig.service, &owner, &other).err(),
        Some(Refusal::ContentUnavailable)
    );

    owner
        .command(Command::Content(DocCommand::CloseViewer(id)))
        .unwrap();
    assert_eq!(authority(&own), Authority::Revoked);
    assert_eq!(authority(&watching), Authority::Revoked);
}

#[tokio::test(start_paused = true)]
async fn editing_an_item_keeps_its_pane_pinned_and_on_the_entry_it_shows() {
    let rig = Rig::new();
    let owner = rig
        .service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    let (item, before) = rig.open(&owner, playlist_in(rig.dir.path(), &["a", "b"]));
    let second = entry_key(&owner.state(), item, 1).entry;
    owner
        .view(
            View {
                active: Some(ViewerId(1)),
                viewers: [(
                    ViewerId(1),
                    ViewerView {
                        pinned: true,
                        entry: Some(second.clone()),
                        ..looking(item)
                    },
                )]
                .into(),
                layout: Some(*pane(1)),
                ..View::default()
            },
            2,
        )
        .unwrap();

    let mut edited = owner.state().doc.workspace.workspace.items[0].clone();
    let Source::Playlist(recipe) = &mut edited.recipe.source else {
        panic!("a playlist");
    };
    recipe.settings.insert(
        "options".into(),
        serde_json::json!({"width": 32, "height": 24}),
    );
    owner
        .command(Command::Content(DocCommand::EditItem {
            item: Box::new(edited),
            content: None,
        }))
        .unwrap();
    rig.jobs.finish_all();

    let state = owner.state();
    assert_ne!(
        entry_key(&state, item, 1).generation,
        before.generation,
        "the edit resolved the item again"
    );
    let shown = &state.presenter.viewers[&ViewerId(1)];
    assert!(shown.pinned, "the pane stays pinned");
    assert_eq!(shown.entry.as_ref(), Some(&second), "on the same entry");
}

#[tokio::test(start_paused = true)]
async fn the_listing_revision_rises_with_the_list_and_not_with_a_cursor() {
    let rig = Rig::new();
    let mut listed = rig.service.listing_revisions();
    let (owner, guest) = rig.shared(Access::Watch);
    assert!(
        listed.has_changed().unwrap(),
        "a session, shared and joined"
    );
    listed.borrow_and_update();
    guest.move_cursor(Some(1));
    assert!(!listed.has_changed().unwrap());
    guest.leave();
    assert!(listed.has_changed().unwrap(), "a seat left");
    listed.borrow_and_update();
    owner.leave();
    assert!(listed.has_changed().unwrap(), "the session ended");
}

#[tokio::test(start_paused = true)]
async fn inspection_permission_tracks_the_seats_current_view_without_restricting_owner_seats() {
    use sessions::session::Overlay;
    for access in [Access::Watch, Access::Explore] {
        let rig = Rig::new();
        let (owner, guest) = rig.shared(access);
        let (_, key) = rig.open_video(&owner, "a.mp4");
        let watching = stream(&rig.service, &guest, &key).unwrap();
        let own = stream(&rig.service, &owner, &key).unwrap();
        let allowed = watching.inspection_permission();
        assert!(allowed());
        owner
            .command(Command::SetOverlay {
                overlay: Overlay::ObjectDetails,
                shown: false,
            })
            .unwrap();
        assert!(
            !allowed(),
            "a following Guest cannot inspect when details are hidden"
        );
        assert!(
            (own.inspection_permission())(),
            "Owner inspection is independent of the Presenter toggle"
        );
        if access == Access::Explore {
            guest
                .command(Command::SetOverlay {
                    overlay: Overlay::ObjectDetails,
                    shown: true,
                })
                .unwrap();
            assert!(allowed(), "an explorer uses its own view");
            guest.command(Command::Follow).unwrap();
            assert!(!allowed(), "following returns to the Presenter permission");
        }
        owner
            .command(Command::SetOverlay {
                overlay: Overlay::ObjectDetails,
                shown: true,
            })
            .unwrap();
        assert!(allowed());
        drop(watching);
        assert!(!allowed(), "a released stream cannot retain permission");
    }
}
