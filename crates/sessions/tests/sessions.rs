//! The sessions crate's rules: grace and leases, explore and restrict, links and cursors, on paused time.

use sessions::{
    Connection, CursorOptions, EndReason, JoinLink, Kind, Mode, Options, Owner, Personal, Role,
    Seat, SeatMessage, SessionError, Sessions, Update, View, Violation, Watcher,
};
use std::time::Duration;
use tokio::{
    task::yield_now,
    time::{advance, timeout},
};

const GRACE: Duration = Duration::from_secs(30);

/// Which sources are open.
#[derive(Clone, Debug, Default, PartialEq)]
struct Open(Vec<u8>);

impl View for Open {
    fn restrict(&mut self, presenter: &Self) {
        self.0.retain(|source| presenter.0.contains(source));
    }
}

type Test = Sessions<Open, u8>;

fn registry() -> Test {
    Sessions::new(GRACE)
}

fn me(seat: &Connection<Open, u8>) -> Personal<Open> {
    match seat.snapshots().current() {
        Update::Snapshot(personal) => personal,
        Update::Ended(reason) => panic!("ended: {reason:?}"),
    }
}

fn watcher(seat: Seat<Open, u8>) -> Watcher<Open, u8> {
    match seat {
        Seat::Watcher(watcher) => watcher,
        Seat::Owner(_) => panic!("expected a watcher"),
    }
}

fn owner(seat: Seat<Open, u8>) -> Owner<Open, u8> {
    match seat {
        Seat::Owner(owner) => owner,
        Seat::Watcher(_) => panic!("expected the owner"),
    }
}

#[tokio::test(start_paused = true)]
async fn a_seat_keeps_its_name_and_color_through_the_grace_period() {
    let sessions = registry();
    let host = sessions.create(CursorOptions::default());
    let a = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    let b = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    let before = me(&a);
    assert_eq!(
        (
            before.participants[0].name.as_str(),
            before.participants[1].name.as_str()
        ),
        ("Guest 1", "Guest 2")
    );
    let color = before.participants[1].color;
    let old_token = a.resume_token().clone();

    drop(a);
    yield_now().await; // let the grace timer register before time moves
    assert!(!me(&host).participants[1].present);
    advance(GRACE - Duration::from_secs(1)).await;
    let a = watcher(sessions.resume(&old_token).unwrap());
    let after = me(&a);
    assert_eq!(after.participants[1].name, "Guest 2");
    assert_eq!(after.participants[1].color, color);
    assert!(after.participants[1].present);
    assert_eq!(
        sessions.resume(&old_token).err(),
        Some(SessionError::NotFound)
    );

    // The first drop's timer is stale now and must not remove the resumed seat.
    advance(GRACE).await;
    assert_eq!(me(&host).participants.len(), 3);

    // A seat that stays away loses its place, and its name is the lowest free one.
    drop(b);
    yield_now().await;
    advance(GRACE + Duration::from_secs(1)).await;
    yield_now().await; // let the expired timer run
    assert_eq!(me(&host).participants.len(), 2);
    let c = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    assert_eq!(me(&c).participants[2].name, "Guest 3");
    assert_eq!(me(&c).participants[2].kind, Kind::Watcher);
}

#[tokio::test(start_paused = true)]
async fn the_owner_ending_or_staying_away_ends_the_session() {
    let sessions = registry();
    let host = sessions.create(CursorOptions::default());
    let guest = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    let mut feed = guest.snapshots();

    drop(host);
    yield_now().await;
    let Update::Snapshot(frozen) = feed.next().await else {
        panic!("expected a snapshot")
    };
    assert!(!frozen.owner_present);
    advance(GRACE + Duration::from_secs(1)).await;
    assert_eq!(feed.next().await, Update::Ended(EndReason::OwnerGone));
    assert!(sessions.list().is_empty());

    let host = sessions.create(CursorOptions::default());
    let guest = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    let mut feed = guest.snapshots();
    host.leave();
    assert_eq!(feed.next().await, Update::Ended(EndReason::Stopped));
    assert!(sessions.list().is_empty());
}

#[tokio::test(start_paused = true)]
async fn regenerating_the_link_rejects_the_old_one_and_keeps_the_members() {
    let sessions = registry();
    let host = sessions.create(CursorOptions::default());
    let old: JoinLink = host.link().to_string().parse().unwrap();
    let guest = sessions.join(&old, CursorOptions::default()).unwrap();

    let new = host.regenerate_link();
    assert_ne!(new, old);
    assert_eq!(sessions.list()[0].link, new);
    assert_eq!(
        sessions.join(&old, CursorOptions::default()).err(),
        Some(SessionError::Rejected)
    );
    assert_eq!(me(&guest).participants.len(), 2);
    assert!(sessions.join(&new, CursorOptions::default()).is_ok());
}

#[tokio::test(start_paused = true)]
async fn exploring_is_the_owners_choice_and_limited_to_what_the_owner_has_open() {
    let sessions = registry();
    let host = sessions.create(CursorOptions::default());
    let guest = sessions
        .watch(host.session(), CursorOptions::default())
        .unwrap();
    host.publish(Open(vec![1, 2]));

    // Off by default; a watcher can never publish, and a refused call changes nothing.
    let revision = me(&guest).revision;
    assert_eq!(guest.explore(), Err(Violation::ExploreOff));
    assert_eq!(
        guest.apply(SeatMessage::Publish(Open(vec![9]))),
        Err(Violation::Role)
    );
    assert_eq!(
        guest.set_view(Open(vec![1]), 1),
        Err(Violation::NotExploring)
    );
    assert_eq!(host.apply(SeatMessage::Explore), Err(Violation::Role));
    assert_eq!(me(&guest).revision, revision);
    assert_eq!(me(&guest).presenter, Open(vec![1, 2]));

    host.set_options(Options { explore: true });
    guest.explore().unwrap();
    assert_eq!(me(&guest).view, Open(vec![1, 2]));
    guest.set_view(Open(vec![2, 3]), 7).unwrap();
    let seen = me(&guest);
    assert_eq!(
        (seen.view, seen.view_seq, seen.mode),
        (Open(vec![2]), 7, Mode::Exploring)
    );
    assert_eq!(me(&host).view, Open(vec![1, 2]));

    // The owner closes a source: every exploring seat is restricted at once.
    host.publish(Open(vec![1]));
    assert_eq!(me(&guest).view, Open(vec![]));

    // Turning explore off moves explorers back and they end up showing the presenter.
    host.set_options(Options { explore: false });
    let seen = me(&guest);
    assert_eq!((seen.mode, seen.view), (Mode::Following, Open(vec![1])));
    assert_eq!(guest.explore(), Err(Violation::ExploreOff));
}

#[tokio::test(start_paused = true)]
async fn only_the_newest_connection_of_a_seat_can_act() {
    let sessions = registry();
    let first = sessions.create(CursorOptions::default());
    let mut old_feed = first.snapshots();
    let token = first.resume_token().clone();

    let second = owner(sessions.resume(&token).unwrap());
    let third = owner(sessions.resume(second.resume_token()).unwrap());
    assert_eq!(old_feed.next().await, Update::Ended(EndReason::Replaced));

    // The replaced handles' calls and their delayed drops do nothing.
    first.publish(Open(vec![1]));
    second.publish(Open(vec![2]));
    drop(first);
    drop(second);
    yield_now().await;
    advance(GRACE * 2).await;
    yield_now().await;
    assert_eq!(sessions.list().len(), 1);
    let seen = me(&third);
    assert!(seen.owner_present);
    assert_eq!(seen.presenter, Open(vec![]));
    third.publish(Open(vec![3]));
    assert_eq!(me(&third).presenter, Open(vec![3]));
    assert_eq!(sessions.resume(&token).err(), Some(SessionError::NotFound));
}

#[tokio::test(start_paused = true)]
async fn cursors_follow_each_seats_share_and_show_options() {
    let sessions = registry();
    let host = sessions.create(CursorOptions::default());
    let hidden = CursorOptions {
        show: false,
        share: true,
    };
    let quiet = CursorOptions {
        show: true,
        share: false,
    };
    let blind = sessions.watch(host.session(), hidden).unwrap();
    let shy = sessions.watch(host.session(), quiet).unwrap();
    let (mut host_sees, mut blind_sees) = (host.cursors(), blind.cursors());

    shy.move_cursor(Some(1));
    host.move_cursor(Some(2));
    blind.move_cursor(Some(3));
    let event = host_sees.recv().await.unwrap();
    assert_eq!(
        (event.participant, event.at),
        (blind.participant(), Some(3))
    );
    assert!(
        timeout(Duration::from_secs(1), blind_sees.recv())
            .await
            .is_err()
    );

    // Switching share off hides the cursor once; after that nothing is sent.
    blind.set_cursor_options(CursorOptions {
        show: false,
        share: false,
    });
    let event = host_sees.recv().await.unwrap();
    assert_eq!((event.participant, event.at), (blind.participant(), None));
    blind.move_cursor(Some(4));
    assert!(
        timeout(Duration::from_secs(1), host_sees.recv())
            .await
            .is_err()
    );
    assert!(!me(&host).participants[1].shares_cursor);
}
