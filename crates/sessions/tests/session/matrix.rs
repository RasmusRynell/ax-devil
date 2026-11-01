//! Every command against every kind of seat, on the pure transition: the table of the design, not the code's.

use sessions::{
    OwnerId, SessionId,
    session::{
        Access, Command, Completion, ContentRef, Control, Fresh, Input, Lease, OWNER_SEAT, Outcome,
        Refusal, SeatId, SessionState, View, Work, authorize, authorize_stream, can, transition,
    },
};
use workspace::{ItemId, WorkspaceItem};
use workspace_store::{DocCommand, HiddenRef};

/// A seat's row in the role table.
#[derive(Clone, Copy, Debug)]
enum Row {
    Owner,
    WatchFollowing,
    ExploreFollowing,
    ExploreExploring,
    Edit,
}

/// What a command needs, as the table names it.
#[derive(Clone, Copy, Debug)]
enum Needs {
    Content,
    Host,
    Control,
    Follow,
    Stream,
}

fn allowed(row: Row, needs: Needs) -> bool {
    match (row, needs) {
        (Row::Owner, _) | (_, Needs::Stream) => true,
        (Row::ExploreFollowing | Row::ExploreExploring, Needs::Follow) => true,
        (Row::Edit, Needs::Content) => true,
        (
            Row::WatchFollowing | Row::ExploreFollowing | Row::ExploreExploring | Row::Edit,
            Needs::Content | Needs::Host | Needs::Control | Needs::Follow,
        ) => false,
    }
}

struct World {
    state: SessionState,
    item: ItemId,
    key: ContentRef,
    spare: WorkspaceItem,
    _dir: tempfile::TempDir,
}

fn step(state: &mut SessionState, input: Input) -> sessions::session::Effects {
    let (next, effects) = transition(state, input).unwrap();
    *state = next;
    effects
}

fn command(seat: SeatId, command: Command) -> Input {
    Input::Command {
        seat,
        lease: Lease(0),
        command,
        fresh: Fresh::now(),
    }
}

/// A shared session of one video, open and resolved, with one watcher (seat 2) under `access`.
fn world(access: Access) -> World {
    let dir = tempfile::tempdir().unwrap();
    let (item, spare) = (
        crate::video_in(dir.path(), "a.mp4"),
        crate::video_in(dir.path(), "b.mp4"),
    );
    let id = item.id;
    let mut state = SessionState::new(SessionId::generate(), OwnerId::generate(), true);
    let add = DocCommand::AddItems {
        items: vec![item],
        open: true,
    };
    let effects = step(&mut state, command(OWNER_SEAT, Command::Content(add)));
    let spawn = &effects.spawn[0];
    let Work::Resolve(job) = &spawn.work;
    let done = Completion::Resolved(crate::resolve(job));
    step(
        &mut state,
        Input::Complete {
            job: spawn.job,
            completion: done,
        },
    );
    step(
        &mut state,
        command(OWNER_SEAT, Command::Control(Control::Share { access })),
    );
    let effects = step(&mut state, Input::Join { secret: None });
    assert!(matches!(effects.outcome, Outcome::Joined { seat, .. } if seat == SeatId(2)));
    let key = crate::key(&state, id);
    World {
        state,
        item: id,
        key,
        spare,
        _dir: dir,
    }
}

/// One sample of every kind of command, with what it needs. The matrix runs exactly this list, so a command added
/// here is covered at once; `Command::needs` is exhaustive, so production code cannot add one without a scope.
fn samples(world: &World) -> Vec<(Command, Needs)> {
    let host = DocCommand::AddItems {
        items: vec![world.spare.clone()],
        open: false,
    };
    vec![
        (Command::Content(host), Needs::Host),
        (
            Command::Content(DocCommand::OpenViewer(world.item)),
            Needs::Content,
        ),
        (
            Command::Content(DocCommand::CloseViewer(world.item)),
            Needs::Content,
        ),
        (
            Command::Content(DocCommand::SetHidden {
                target: HiddenRef::Entry(world.key.entry.clone()),
                hidden: true,
            }),
            Needs::Content,
        ),
        (Command::Follow, Needs::Follow),
        (
            Command::Control(Control::Share {
                access: Access::Edit,
            }),
            Needs::Control,
        ),
        (
            Command::Control(Control::SetAccess {
                access: Access::Watch,
            }),
            Needs::Control,
        ),
        (Command::Control(Control::RegenerateLink), Needs::Control),
        (Command::Control(Control::Stop), Needs::Control),
        (Command::RequestTicket(world.key.clone()), Needs::Stream),
    ]
}

/// The seat of `row` and the state it sits in.
fn seated(row: Row) -> (World, SeatId) {
    let explore = |world: &mut World| {
        let view = Input::View {
            seat: SeatId(2),
            lease: Lease(0),
            view: View::default(),
            seq: 1,
            now_us: 0,
        };
        step(&mut world.state, view);
    };
    match row {
        Row::Owner => (world(Access::Watch), OWNER_SEAT),
        Row::WatchFollowing => (world(Access::Watch), SeatId(2)),
        Row::ExploreFollowing => (world(Access::Explore), SeatId(2)),
        Row::ExploreExploring => {
            let mut world = world(Access::Explore);
            explore(&mut world);
            (world, SeatId(2))
        }
        Row::Edit => (world(Access::Edit), SeatId(2)),
    }
}

#[test]
fn each_kind_of_seat_may_run_exactly_the_commands_the_table_gives_it() {
    for row in [
        Row::Owner,
        Row::WatchFollowing,
        Row::ExploreFollowing,
        Row::ExploreExploring,
        Row::Edit,
    ] {
        let (world, seat) = seated(row);
        for (sample, needs) in samples(&world) {
            let result = transition(&world.state, command(seat, sample.clone()));
            let denied = matches!(result, Err(Refusal::NotAllowed));
            assert_eq!(
                !denied,
                allowed(row, needs),
                "{row:?} asking {sample:?} (needs {needs:?}): {:?}",
                result.map(|_| ())
            );
            // The refusal comes from `authorize` itself, not from the command after it, and the flags the seat is
            // told are the same table.
            let authorized = authorize(&world.state, seat, Lease(0), sample.needs()).is_ok();
            assert_eq!(
                authorized,
                allowed(row, needs),
                "{row:?} authorize {sample:?}"
            );
            let can = can(&world.state, seat, Lease(0)).unwrap();
            match needs {
                Needs::Content => assert_eq!(can.content, allowed(row, needs), "{row:?}"),
                Needs::Host => assert_eq!(can.host, allowed(row, needs), "{row:?}"),
                Needs::Control => assert_eq!(can.control, allowed(row, needs), "{row:?}"),
                Needs::Follow | Needs::Stream => {}
            }
        }
    }
}

#[test]
fn a_view_changes_the_presenter_only_for_the_owner_and_starts_exploring_for_a_seat_that_may() {
    let sets = |row: Row| {
        let (world, seat) = seated(row);
        let view = Input::View {
            seat,
            lease: Lease(0),
            view: View::default(),
            seq: 1,
            now_us: 0,
        };
        transition(&world.state, view)
    };

    assert!(matches!(sets(Row::Owner), Ok((_, fx)) if matches!(fx.outcome, Outcome::None)));
    let (watching, effects) = sets(Row::WatchFollowing).unwrap();
    assert!(matches!(
        effects.outcome,
        Outcome::ViewRefused(Refusal::NotAllowed)
    ));
    assert_eq!(
        watching.seats[&SeatId(2)].views_done,
        1,
        "a refused view is settled, so the client stops showing it"
    );
    for row in [Row::ExploreFollowing, Row::ExploreExploring, Row::Edit] {
        let (state, _) = sets(row).unwrap();
        assert_eq!(
            state.seats[&SeatId(2)].mode(),
            sessions::SeatMode::Exploring,
            "{row:?}"
        );
    }
}

#[test]
fn streams_are_bound_by_seat_following_the_presenter_or_choosing_their_own_frames() {
    use playback::{Authority, Priority};
    let authority = |row: Row| {
        let (world, seat) = seated(row);
        authorize_stream(&world.state, seat, Lease(0), &world.key).unwrap()
    };

    assert_eq!(
        authority(Row::Owner),
        Authority::Own {
            priority: Priority::High
        }
    );
    for row in [Row::WatchFollowing, Row::ExploreFollowing] {
        assert!(
            matches!(authority(row), Authority::Follow { .. }),
            "{row:?}"
        );
    }
    for row in [Row::ExploreExploring, Row::Edit] {
        let low = Authority::Own {
            priority: Priority::Low,
        };
        assert_eq!(authority(row), low, "{row:?}");
    }
}
