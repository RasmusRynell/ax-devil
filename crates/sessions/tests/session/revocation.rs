//! What makes authority end for good: Stop, close and reopen, a replaced seat, lowered access and the end of the
//! session. Each is checked on the stream a watcher holds, the seat it sits in and the secrets that could revive it.

use crate::{GRACE, Rig, authority, entry_key, key, link, playlist_in, stream};
use playback::{Authority, Priority};
use sessions::{
    CursorOptions, EndReason, MAX_SESSIONS,
    session::{Access, Change, Command, Control, Refusal, View, ViewerView},
};
use tokio::{task::yield_now, time::advance};
use workspace_store::DocCommand;

const STOP: Command = Command::Control(Control::Stop);

#[tokio::test(start_paused = true)]
async fn stopping_ends_every_watcher_and_restarting_revives_nothing() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let first = stream(&rig.service, &watcher, &key).unwrap();
    let Ok(sessions::session::Reply::Ticket(unused)) =
        watcher.command(Command::RequestTicket(key.clone()))
    else {
        panic!("a ticket")
    };
    let (old_link, old_token, old_seat) =
        (link(&owner), watcher.resume_token().clone(), watcher.seat());
    let mut feed = watcher.feed();

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
    let (owner, watcher) = rig.shared(Access::Watch);
    let (item, old_key) = rig.open_video(&owner, "a.mp4");
    let before = stream(&rig.service, &watcher, &old_key).unwrap();

    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();
    assert_eq!(authority(&before), Authority::Revoked);

    owner
        .command(Command::Content(DocCommand::OpenViewer(item)))
        .unwrap();
    rig.jobs.finish_all();
    let new_key = key(&owner.state(), item);
    assert_ne!(new_key, old_key, "the same Content under a new generation");
    assert!(matches!(
        stream(&rig.service, &watcher, &old_key),
        Err(Refusal::ContentUnavailable)
    ));
    let after = stream(&rig.service, &watcher, &new_key).unwrap();
    assert!(
        after.grant() > before.grant(),
        "a grant id is never reissued"
    );
    assert!(matches!(authority(&after), Authority::Follow { .. }));
}

#[tokio::test(start_paused = true)]
async fn a_seat_taken_over_by_a_newer_connection_loses_its_streams_and_its_say() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let old = stream(&rig.service, &watcher, &key).unwrap();
    let mut feed = watcher.feed();

    let newer = rig.service.resume(watcher.resume_token()).unwrap();

    assert_eq!(authority(&old), Authority::Revoked);
    assert!(matches!(
        feed.next().await,
        Change::Ended(EndReason::Replaced)
    ));
    assert_eq!(
        watcher.command(Command::RequestTicket(key.clone())).err(),
        Some(Refusal::SeatGone)
    );
    assert!(stream(&rig.service, &newer, &key).is_ok());
}

#[tokio::test(start_paused = true)]
async fn lowering_access_takes_back_exploring_and_editing_at_once() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Explore);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    watcher.view(View::default(), 1).unwrap();
    let exploring = stream(&rig.service, &watcher, &key).unwrap();
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
        watcher.view(View::default(), 2).err(),
        Some(Refusal::NotAllowed)
    );

    owner
        .command(Command::Control(Control::SetAccess {
            access: Access::Edit,
        }))
        .unwrap();
    let item = *owner.state().doc.workspace.open.iter().next().unwrap();
    assert!(
        watcher
            .command(Command::Content(DocCommand::CloseViewer(item)))
            .is_ok()
    );
    owner
        .command(Command::Control(Control::SetAccess {
            access: Access::Explore,
        }))
        .unwrap();
    assert_eq!(
        watcher
            .command(Command::Content(DocCommand::OpenViewer(item)))
            .err(),
        Some(Refusal::NotAllowed)
    );
}

#[tokio::test(start_paused = true)]
async fn the_session_ending_ends_every_stream_job_and_seat() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let held = stream(&rig.service, &watcher, &key).unwrap();
    owner
        .command(Command::Content(DocCommand::AddItems {
            items: vec![rig.video("b.mp4")],
            open: true,
        }))
        .unwrap();
    let pending = rig.jobs.take();
    assert_eq!(pending.len(), 1);
    let mut feed = watcher.feed();
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
        .map(|_| rig.service.create(true, cursors()).unwrap())
        .collect();
    assert_eq!(
        rig.service.create(true, cursors()).err(),
        Some(Refusal::Full)
    );

    // Every window disconnects; each session is kept for its grace period, so there is still no room.
    owners.drain(..).for_each(drop);
    yield_now().await; // let the grace timers register before time moves
    advance(GRACE / 2).await;
    assert_eq!(
        rig.service.create(true, cursors()).err(),
        Some(Refusal::Full)
    );

    // Once they have expired, the places are free again.
    advance(GRACE).await;
    yield_now().await;
    assert!(rig.service.create(true, cursors()).is_ok());
}

#[tokio::test(start_paused = true)]
async fn a_refused_command_and_a_refused_view_change_nothing_a_seat_can_see() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (item, _) = rig.open_video(&owner, "a.mp4");
    let before = watcher.state();

    let refused = watcher.command(Command::Content(DocCommand::CloseViewer(item)));
    let view = watcher.view(View::default(), 7);

    assert_eq!(refused.err(), Some(Refusal::NotAllowed));
    assert_eq!(view.err(), Some(Refusal::NotAllowed));
    let after = watcher.state();
    assert_eq!(before.doc_rev, after.doc_rev);
    assert_eq!(before.presenter_rev, after.presenter_rev);
    assert_eq!(
        after.seats[&watcher.seat()].views_done,
        7,
        "a refused view is settled, so the client stops showing it"
    );
}

#[tokio::test(start_paused = true)]
async fn closing_a_viewer_removes_it_from_every_view() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Explore);
    let (item, _) = rig.open_video(&owner, "a.mp4");
    let showing = View {
        active: Some(item),
        viewers: [(item, ViewerView::default())].into(),
        ..View::default()
    };
    owner.view(showing.clone(), 1).unwrap();
    watcher.view(showing, 1).unwrap();
    assert_eq!(owner.state().presenter.active, Some(item));

    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();

    let state = watcher.state();
    let own = state.seats[&watcher.seat()].view.as_ref().unwrap();
    assert!(own.viewers.is_empty() && own.active.is_none());
    assert!(state.presenter.viewers.is_empty() && state.presenter.active.is_none());
}

#[tokio::test(start_paused = true)]
async fn a_new_link_turns_away_the_old_one_and_keeps_the_seats_already_in() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
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
    assert!(owner.state().seats.contains_key(&watcher.seat()));
}

#[tokio::test(start_paused = true)]
async fn a_follower_streams_only_the_entry_the_presenter_shows_and_loses_it_when_the_presenter_switches()
 {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let (item, first) = rig.open(&owner, playlist_in(rig.dir.path(), &["a", "b"]));
    let second = entry_key(&owner.state(), item, 1);
    let showing = |entry: &workspace::ContentId| View {
        active: Some(item),
        viewers: [(
            item,
            ViewerView {
                clock: None,
                entry: Some(entry.clone()),
            },
        )]
        .into(),
        ..View::default()
    };

    // Until the presenter picks an entry, the first one is shown.
    let following = stream(&rig.service, &watcher, &first).unwrap();
    assert!(matches!(authority(&following), Authority::Follow { .. }));
    assert_eq!(
        stream(&rig.service, &watcher, &second).err(),
        Some(Refusal::ContentUnavailable),
        "a follower gets no ticket for an entry the presenter does not show"
    );
    let own = stream(&rig.service, &owner, &second).unwrap();
    assert_eq!(own.entry().id, second.entry, "the owner streams any entry");

    owner.view(showing(&second.entry), 1).unwrap();

    assert_eq!(authority(&following), Authority::Revoked);
    assert!(matches!(
        authority(&stream(&rig.service, &watcher, &second).unwrap()),
        Authority::Follow { .. }
    ));
    assert_eq!(
        stream(&rig.service, &watcher, &first).err(),
        Some(Refusal::ContentUnavailable)
    );
}
