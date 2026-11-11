//! What makes authority end for good: Stop, close and reopen, a replaced seat, lowered access and the end of the
//! session. Each is checked on the stream a guest holds, the seat it sits in and the secrets that could revive it.

use crate::{
    GRACE, Rig, authority, entry_key, key, link, looking, pane, playlist_in, showing, stream,
};
use playback::{Authority, Priority};
use sessions::{
    CursorOptions, EndReason, MAX_SESSIONS,
    session::{
        Access, Axis, Change, Command, ContentRef, Control, Layout, Refusal, View, ViewerId,
        ViewerView,
    },
};
use tokio::{task::yield_now, time::advance};
use workspace_store::DocCommand;

const STOP: Command = Command::Control(Control::Stop);

#[tokio::test(start_paused = true)]
async fn stopping_ends_every_watcher_and_restarting_revives_nothing() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let first = stream(&rig.service, &guest, &key).unwrap();
    let Ok(sessions::session::Reply::Ticket(unused)) =
        guest.command(Command::RequestTicket(key.clone()))
    else {
        panic!("a ticket")
    };
    let (old_link, old_token, old_seat) =
        (link(&owner), guest.resume_token().clone(), guest.seat());
    let mut feed = guest.feed();

    owner.command(STOP).unwrap();

    assert_eq!(authority(&first), Authority::Revoked);
    assert!(matches!(
        feed.next().await,
        Change::Ended(EndReason::Stopped)
    ));
    assert!(matches!(
        rig.service.resume(&old_token),
        Err(Refusal::SeatGone)
    ));
    assert!(matches!(
        rig.service.join(&old_link, CursorOptions::default()),
        Err(Refusal::Link)
    ));
    assert!(
        owner.state().sharing.is_none(),
        "the session itself goes on"
    );
    assert!(
        owner
            .command(Command::Content(DocCommand::CloseViewer(
                owner
                    .state()
                    .doc
                    .workspace
                    .open
                    .iter()
                    .copied()
                    .next()
                    .unwrap()
            )))
            .is_ok()
    );

    owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    assert_ne!(link(&owner).secret, old_link.secret);
    assert!(matches!(
        rig.service.join(&old_link, CursorOptions::default()),
        Err(Refusal::Link)
    ));
    assert_eq!(rig.service.redeem(&unused).err(), Some(Refusal::BadTicket));
    let again = rig.join(&owner);
    assert!(again.seat() > old_seat, "a seat is never reissued");
    assert_eq!(
        authority(&first),
        Authority::Revoked,
        "and revoked stays revoked"
    );
}

#[tokio::test(start_paused = true)]
async fn closing_a_viewer_ends_its_streams_and_reopening_needs_a_key_of_the_new_generation() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (item, old_key) = rig.open_video(&owner, "a.mp4");
    let before = stream(&rig.service, &guest, &old_key).unwrap();

    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();
    assert_eq!(authority(&before), Authority::Revoked);

    owner
        .command(Command::Content(DocCommand::OpenViewer(item)))
        .unwrap();
    rig.jobs.finish_all();
    owner.view(showing(item), 2).unwrap();
    let new_key = key(&owner.state(), item);
    assert_ne!(new_key, old_key, "the same Content under a new generation");
    assert!(matches!(
        stream(&rig.service, &guest, &old_key),
        Err(Refusal::ContentUnavailable)
    ));
    let after = stream(&rig.service, &guest, &new_key).unwrap();
    assert!(
        after.grant() > before.grant(),
        "a grant id is never reissued"
    );
    assert!(matches!(authority(&after), Authority::Follow { .. }));
}

#[tokio::test(start_paused = true)]
async fn a_seat_taken_over_by_a_newer_connection_loses_its_streams_and_its_say() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let old = stream(&rig.service, &guest, &key).unwrap();
    let mut feed = guest.feed();

    let newer = rig.service.resume(guest.resume_token()).unwrap();

    assert_eq!(authority(&old), Authority::Revoked);
    assert!(matches!(
        feed.next().await,
        Change::Ended(EndReason::Replaced)
    ));
    assert_eq!(
        guest.command(Command::RequestTicket(key.clone())).err(),
        Some(Refusal::SeatGone)
    );
    assert!(stream(&rig.service, &newer, &key).is_ok());
}

#[tokio::test(start_paused = true)]
async fn lowering_access_takes_back_exploring_at_once() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Explore);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    guest.view(View::default(), 1).unwrap();
    let exploring = stream(&rig.service, &guest, &key).unwrap();
    assert_eq!(
        authority(&exploring),
        Authority::Own {
            priority: Priority::Low
        }
    );

    owner
        .command(Command::Control(Control::SetAccess {
            access: Access::Watch,
        }))
        .unwrap();

    assert!(matches!(authority(&exploring), Authority::Follow { .. }));
    assert_eq!(
        guest.view(View::default(), 2).err(),
        Some(Refusal::NotAllowed)
    );

    let item = *owner.state().doc.workspace.open.iter().next().unwrap();
    assert_eq!(
        guest
            .command(Command::Content(DocCommand::OpenViewer(item)))
            .err(),
        Some(Refusal::NotAllowed)
    );
}

#[tokio::test(start_paused = true)]
async fn the_session_ending_ends_every_stream_job_and_seat() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let held = stream(&rig.service, &guest, &key).unwrap();
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![rig.video("b.mp4")],
            open: true,
        }))
        .unwrap();
    let pending = rig.jobs.take();
    assert_eq!(pending.len(), 1);
    let mut feed = guest.feed();
    let token = owner.resume_token().clone();

    drop(owner);
    yield_now().await; // let the grace timer register before time moves
    advance(GRACE).await;
    yield_now().await;

    assert_eq!(authority(&held), Authority::Revoked);
    assert!(matches!(
        feed.next().await,
        Change::Ended(EndReason::OwnerGone)
    ));
    assert!(pending[0].0.cancel.is_cancelled());
    assert!(matches!(rig.service.resume(&token), Err(Refusal::SeatGone)));
}

#[tokio::test(start_paused = true)]
async fn disconnected_sessions_in_their_grace_period_still_count_against_the_server_limit() {
    let rig = Rig::new();
    let cursors = CursorOptions::default;
    let mut owners: Vec<_> = (0..MAX_SESSIONS)
        .map(|_| {
            rig.service
                .create(true, cursors(), Default::default())
                .unwrap()
        })
        .collect();
    assert_eq!(
        rig.service
            .create(true, cursors(), Default::default())
            .err(),
        Some(Refusal::Full)
    );

    // Every window disconnects; each session is kept for its grace period, so there is still no room.
    owners.drain(..).for_each(drop);
    yield_now().await; // let the grace timers register before time moves
    advance(GRACE / 2).await;
    assert_eq!(
        rig.service
            .create(true, cursors(), Default::default())
            .err(),
        Some(Refusal::Full)
    );

    // Once they have expired, the places are free again.
    advance(GRACE).await;
    yield_now().await;
    assert!(
        rig.service
            .create(true, cursors(), Default::default())
            .is_ok()
    );
}

#[tokio::test(start_paused = true)]
async fn a_refused_command_and_a_refused_view_change_nothing_a_seat_can_see() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (item, _) = rig.open_video(&owner, "a.mp4");
    let before = guest.state();

    let refused = guest.command(Command::Content(DocCommand::CloseViewer(item)));
    let view = guest.view(View::default(), 7);

    assert_eq!(refused.err(), Some(Refusal::NotAllowed));
    assert_eq!(view.err(), Some(Refusal::NotAllowed));
    let after = guest.state();
    assert_eq!(before.doc_rev, after.doc_rev);
    assert_eq!(before.presenter_rev, after.presenter_rev);
    assert_eq!(
        after.seats[&guest.seat()].views_done,
        7,
        "a refused view is settled, so the client stops showing it"
    );
}

#[tokio::test(start_paused = true)]
async fn closing_a_viewer_removes_it_from_every_view() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Explore);
    let (item, _) = rig.open_video(&owner, "a.mp4");
    // The owner's view from opening shows the item; the exploring guest shows it in its own.
    guest.view(showing(item), 1).unwrap();
    assert_eq!(owner.state().presenter.active, Some(ViewerId(1)));

    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();

    let state = guest.state();
    let own = state.seats[&guest.seat()].view.as_ref().unwrap();
    assert!(own.viewers.is_empty() && own.active.is_none());
    assert!(state.presenter.viewers.is_empty() && state.presenter.active.is_none());
}

#[tokio::test(start_paused = true)]
async fn a_new_link_turns_away_the_old_one_and_keeps_the_seats_already_in() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let old = link(&owner);

    owner
        .command(Command::Control(Control::RegenerateLink))
        .unwrap();

    assert!(matches!(
        rig.service.join(&old, CursorOptions::default()),
        Err(Refusal::Link)
    ));
    assert!(
        rig.service
            .join(&link(&owner), CursorOptions::default())
            .is_ok()
    );
    assert!(owner.state().seats.contains_key(&guest.seat()));
}

#[tokio::test(start_paused = true)]
async fn a_follower_streams_only_the_entry_a_presenter_pane_shows_and_losing_it_leaves_the_other_pane_alone()
 {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (item, first) = rig.open(&owner, playlist_in(rig.dir.path(), &["a", "b"]));
    let second = entry_key(&owner.state(), item, 1);
    let beside = ContentRef {
        viewer: ViewerId(2),
        ..second.clone()
    };
    // Two panes show the playlist: viewer 1 as `entry` asks, viewer 2 its second entry.
    let panes = |entry: Option<&workspace::ContentId>| View {
        active: Some(ViewerId(1)),
        viewers: [
            (
                ViewerId(1),
                ViewerView {
                    entry: entry.cloned(),
                    ..looking(item)
                },
            ),
            (
                ViewerId(2),
                ViewerView {
                    entry: Some(second.entry.clone()),
                    ..looking(item)
                },
            ),
        ]
        .into(),
        layout: Some(Layout::Split {
            axis: Axis::Column,
            share: 5000,
            first: pane(1),
            second: pane(2),
        }),
        ..View::default()
    };

    // Until the presenter picks an entry, the first one is shown.
    let following = stream(&rig.service, &guest, &first).unwrap();
    assert!(matches!(authority(&following), Authority::Follow { .. }));
    assert_eq!(
        stream(&rig.service, &guest, &second).err(),
        Some(Refusal::ContentUnavailable),
        "a follower gets no ticket for an entry the presenter does not show"
    );
    let own = stream(&rig.service, &owner, &second).unwrap();
    assert_eq!(
        own.entry().unwrap().id,
        second.entry,
        "the owner streams any entry"
    );

    owner.view(panes(None), 2).unwrap();
    let other = stream(&rig.service, &guest, &beside).unwrap();
    assert!(matches!(authority(&following), Authority::Follow { .. }));
    assert!(matches!(authority(&other), Authority::Follow { .. }));

    owner.view(panes(Some(&second.entry)), 3).unwrap();

    assert_eq!(authority(&following), Authority::Revoked);
    assert!(
        matches!(authority(&other), Authority::Follow { .. }),
        "the other pane of the item keeps its stream"
    );
    assert!(matches!(
        authority(&stream(&rig.service, &guest, &second).unwrap()),
        Authority::Follow { .. }
    ));
    assert_eq!(
        stream(&rig.service, &guest, &first).err(),
        Some(Refusal::ContentUnavailable)
    );
}

#[tokio::test(start_paused = true)]
async fn an_exploring_seat_keeps_its_own_entry_when_the_presenter_changes_entry() {
    let rig = Rig::new();
    let (owner, explorer) = rig.shared(Access::Explore);
    let (item, first) = rig.open(&owner, playlist_in(rig.dir.path(), &["a", "b"]));
    let second = entry_key(&owner.state(), item, 1);
    let showing = |entry: &workspace::ContentId| View {
        active: Some(ViewerId(1)),
        viewers: [(
            ViewerId(1),
            ViewerView {
                entry: Some(entry.clone()),
                ..looking(item)
            },
        )]
        .into(),
        layout: Some(*pane(1)),
        ..View::default()
    };
    owner.view(showing(&first.entry), 1).unwrap();
    explorer.view(showing(&second.entry), 1).unwrap();
    let exploring = stream(&rig.service, &explorer, &second).unwrap();
    let own = Authority::Own {
        priority: Priority::Low,
    };
    assert_eq!(authority(&exploring), own);

    owner.view(showing(&second.entry), 2).unwrap();
    owner.view(showing(&first.entry), 3).unwrap();

    assert_eq!(authority(&exploring), own, "its stream is not revoked");
    let state = explorer.state();
    let view = state.seats[&explorer.seat()].view.as_ref().unwrap();
    assert_eq!(
        view.viewers[&ViewerId(1)].entry.as_ref(),
        Some(&second.entry)
    );
}
