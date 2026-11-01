//! Every command against every kind of seat, on the pure transition: the table of the design, not the code's.

use sessions::{
    OwnerId, SessionId,
    session::{
        Access, Command, Completion, ContentRef, Control, Demand, Fresh, Input, Lease, OWNER_SEAT,
        Outcome, Refusal, SeatId, SessionState, StreamKey, Use, View, Work, authorize, transition,
    },
};
use workspace::{ItemId, WorkspaceItem};
use workspace_store::{DocCommand, Resolution};

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
    key: StreamKey,
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
    let video = |name: &str| {
        let path = dir.path().join(name);
        std::fs::write(&path, "x").unwrap();
        workspace::intake::video(&path).unwrap()
    };
    let (item, spare) = (video("a.mp4"), video("b.mp4"));
    let id = item.id;
    let mut state = SessionState::new(SessionId::generate(), OwnerId::generate(), true);
    let add = DocCommand::AddItems {
        items: vec![item],
        open: true,
    };
    let effects = step(&mut state, command(OWNER_SEAT, Command::Content(add)));
    let spawn = &effects.spawn[0];
    let Work::Resolve(job) = &spawn.work;
    let done = Completion::Resolved(workspace_store::resolve(job));
    step(
        &mut state,
        Input::Complete {
            job: spawn.job,
            completion: done,
        },
    );
    step(
        &mut state,
        command(OWNER_SEAT, Command::Control(Control::Share(access))),
    );
    let effects = step(&mut state, Input::Join { secret: None });
    assert!(matches!(effects.outcome, Outcome::Joined { seat, .. } if seat == SeatId(2)));
    let Resolution::Resolved {
        content,
        generation,
    } = &state.doc.workspace.resolutions[&id]
    else {
        panic!("resolved");
    };
    let key = StreamKey::Frames(ContentRef {
        content: content.id().clone(),
        generation: *generation,
    });
    World {
        state,
        item: id,
        key,
        spare,
        _dir: dir,
    }
}

/// The samples of every kind of command. The match is exhaustive on purpose: a new command or control does not
/// compile until it has a sample here, and the matrix then covers it.
fn samples(of: &Command, world: &World) -> Vec<(Command, Needs)> {
    let host = DocCommand::AddItems {
        items: vec![world.spare.clone()],
        open: false,
    };
    match of {
        Command::Content(DocCommand::AddItems { .. }) => {
            vec![(Command::Content(host), Needs::Host)]
        }
        Command::Content(DocCommand::OpenViewer(_) | DocCommand::CloseViewer(_)) => vec![
            (
                Command::Content(DocCommand::OpenViewer(world.item)),
                Needs::Content,
            ),
            (
                Command::Content(DocCommand::CloseViewer(world.item)),
                Needs::Content,
            ),
        ],
        Command::Follow => vec![(Command::Follow, Needs::Follow)],
        Command::Control(Control::Share(_)) => {
            vec![(
                Command::Control(Control::Share(Access::Edit)),
                Needs::Control,
            )]
        }
        Command::Control(Control::SetAccess(_)) => vec![(
            Command::Control(Control::SetAccess(Access::Watch)),
            Needs::Control,
        )],
        Command::Control(Control::RegenerateLink) => {
            vec![(Command::Control(Control::RegenerateLink), Needs::Control)]
        }
        Command::Control(Control::Stop) => vec![(Command::Control(Control::Stop), Needs::Control)],
        Command::RequestTicket(_) => {
            vec![(Command::RequestTicket(world.key.clone()), Needs::Stream)]
        }
    }
}

/// One command of every kind, for `samples` to expand.
fn kinds(world: &World) -> Vec<Command> {
    vec![
        Command::Content(DocCommand::OpenViewer(world.item)),
        Command::Content(DocCommand::AddItems {
            items: vec![],
            open: false,
        }),
        Command::Follow,
        Command::Control(Control::Share(Access::Watch)),
        Command::Control(Control::SetAccess(Access::Watch)),
        Command::Control(Control::RegenerateLink),
        Command::Control(Control::Stop),
        Command::RequestTicket(world.key.clone()),
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
        for kind in kinds(&world) {
            for (sample, needs) in samples(&kind, &world) {
                let result = transition(&world.state, command(seat, sample.clone()));
                let denied = matches!(result, Err(Refusal::NotAllowed));
                assert_eq!(
                    !denied,
                    allowed(row, needs),
                    "{row:?} asking {sample:?} (needs {needs:?}): {:?}",
                    result.map(|_| ())
                );
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

    assert!(sets(Row::Owner).is_ok());
    assert_eq!(sets(Row::WatchFollowing).err(), Some(Refusal::NotAllowed));
    for row in [Row::ExploreFollowing, Row::ExploreExploring, Row::Edit] {
        let (state, _) = sets(row).unwrap();
        assert_eq!(
            state.seats[&SeatId(2)].mode(),
            sessions::Mode::Exploring,
            "{row:?}"
        );
    }
}

#[test]
fn computing_is_refused_to_every_seat_until_its_policy_is_decided() {
    let (world, _) = seated(Row::Owner);
    let of = ContentRef {
        content: workspace::ContentId::of_item(&world.item),
        generation: workspace_store::ContentGen(1),
    };
    for seat in [OWNER_SEAT, SeatId(2)] {
        let result = authorize(&world.state, seat, Lease(0), Use::Compute(&of));
        assert_eq!(result, Err(Refusal::Unsupported));
    }
}

#[test]
fn streams_are_bound_by_seat_following_the_presenter_or_choosing_their_own_frames() {
    use playback::Priority;
    let demand = |row: Row| {
        let (world, seat) = seated(row);
        authorize(&world.state, seat, Lease(0), Use::Stream(&world.key)).unwrap()
    };

    assert_eq!(
        demand(Row::Owner),
        Demand::Own {
            priority: Priority::High
        }
    );
    assert_eq!(demand(Row::WatchFollowing), Demand::Follow);
    assert_eq!(demand(Row::ExploreFollowing), Demand::Follow);
    for row in [Row::ExploreExploring, Row::Edit] {
        let low = Demand::Own {
            priority: Priority::Low,
        };
        assert_eq!(demand(row), low, "{row:?}");
    }
}
