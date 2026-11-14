//! Any number of Owner seats share one Presenter View, and the session lives while one is attached.

use crate::{showing, video_in};
use sessions::{
    SessionId,
    session::{
        Command, Completion, Control, Fresh, Input, Lease, Outcome, SeatId, SessionState, ViewerId,
        Work, transition,
    },
};
use workspace::store::DocCommand;

fn step(state: &mut SessionState, input: Input) -> Outcome {
    let (next, effects) = transition(state, input).unwrap();
    *state = next;
    effects.outcome
}

fn attach(state: &mut SessionState, full_access: bool) -> SeatId {
    let Outcome::Seated { seat, .. } = step(state, Input::Attach { full_access }) else {
        panic!("attaching seats the caller");
    };
    seat
}

fn leave(state: &mut SessionState, seat: SeatId) {
    step(
        state,
        Input::Leave {
            seat,
            lease: Lease(0),
        },
    );
}

#[test]
fn a_second_owner_seat_drives_the_presenter_view_the_first_shows() {
    let dir = tempfile::tempdir().unwrap();
    let item = video_in(dir.path(), "a.mp4");
    let id = item.id;
    let mut state = SessionState::new(SessionId::generate(), true);
    let first = state.seat_owner();
    let second = attach(&mut state, true);
    let add = DocCommand::AddItems {
        items: vec![item],
        open: true,
    };
    let input = Input::Command {
        seat: second,
        lease: Lease(0),
        command: Command::Content(add),
        fresh: Fresh::now(),
    };
    let (next, effects) = transition(&state, input).unwrap();
    state = next;
    let spawn = &effects.spawn[0];
    let Work::Resolve(job) = &spawn.work;
    let done = Completion::Resolved {
        result: crate::resolve(job),
        validated: Vec::new(),
    };
    step(
        &mut state,
        Input::Complete {
            job: spawn.job,
            completion: done,
        },
    );

    let before = state.presenter_rev;
    let view = Input::View {
        seat: second,
        lease: Lease(0),
        view: showing(id),
        seq: 1,
        now_us: 0,
    };
    step(&mut state, view);
    assert_eq!(state.presenter_rev, before + 1);
    assert_eq!(
        state.presenter_by,
        Some(second),
        "the server stamps who set it"
    );
    assert_eq!(
        state.seats[&first].view, None,
        "the first seat shows the Presenter View"
    );
    assert_eq!(state.presenter.viewers[&ViewerId(1)].item, id);
}

#[test]
fn the_session_lives_until_its_last_owner_seat_leaves_and_a_guest_does_not_keep_it() {
    let mut state = SessionState::new(SessionId::generate(), false);
    let first = state.seat_owner();
    let second = attach(&mut state, false);
    let share = Input::Command {
        seat: second,
        lease: Lease(0),
        command: Command::Control(Control::Share {
            access: sessions::session::Access::Watch,
        }),
        fresh: Fresh::now(),
    };
    step(&mut state, share);
    step(&mut state, Input::Join { secret: None });

    leave(&mut state, first);
    assert!(state.ended.is_none(), "another Owner seat is attached");
    leave(&mut state, second);
    assert!(state.ended.is_some(), "a Guest alone does not keep it");
}

#[test]
fn an_owner_without_full_access_is_a_guest_of_a_session_that_reads_the_environment() {
    let mut state = SessionState::new(SessionId::generate(), true);
    let owner = state.seat_owner();
    assert!(
        transition(&state, Input::Attach { full_access: false }).is_err(),
        "not shared: there is no Guest seat to give"
    );
    let share = Input::Command {
        seat: owner,
        lease: Lease(0),
        command: Command::Control(Control::Share {
            access: sessions::session::Access::Watch,
        }),
        fresh: Fresh::now(),
    };
    step(&mut state, share);
    let guest = attach(&mut state, false);
    assert!(state.seats[&guest].kind.sharing().is_some());
    let owner_seat = attach(&mut state, true);
    assert!(state.seats[&owner_seat].kind.sharing().is_none());
}
