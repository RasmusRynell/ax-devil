//! The roster: who is in the session, with a name and a color each.

use crate::Rig;
use sessions::{
    CursorOptions, SeatRole,
    session::{Access, RosterEntry},
};
use std::sync::Arc;
use tokio::sync::watch::Receiver;

fn listed(roster: &Receiver<Arc<Vec<RosterEntry>>>) -> Vec<(String, u8)> {
    let entries = roster.borrow();
    entries
        .iter()
        .map(|entry| (entry.name.clone(), entry.color))
        .collect()
}

#[tokio::test(start_paused = true)]
async fn seats_are_named_and_colored_with_what_is_free_and_keep_both_for_life() {
    let rig = Rig::new();
    let (owner, first) = rig.shared(Access::Watch);
    let second = rig.join(&owner);
    let roster = owner.roster();
    let guest = |n: u8| (format!("Guest {n}"), n - 1);
    assert_eq!(
        listed(&roster),
        [guest(1), guest(2), guest(3)],
        "oldest first"
    );
    assert_eq!(roster.borrow()[0].kind, SeatRole::Owner);

    // The first watcher leaves; the next one takes the free name and color. A resumed seat keeps its own.
    first.leave();
    let resumed = rig.service.resume(second.resume_token()).unwrap();
    let third = rig.join(&owner);
    assert_eq!(listed(&roster), [guest(1), guest(3), guest(2)]);
    drop((resumed, third));
}

#[tokio::test(start_paused = true)]
async fn switching_the_cursor_off_reaches_the_roster() {
    let rig = Rig::new();
    let (owner, watcher) = rig.shared(Access::Watch);
    let mut roster = owner.roster();
    roster.mark_unchanged();
    watcher.set_cursor_options(CursorOptions {
        show: true,
        share: false,
    });
    assert!(roster.has_changed().unwrap());
    assert!(!roster.borrow()[1].shares_cursor);
}
