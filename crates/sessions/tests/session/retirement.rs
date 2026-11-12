//! Pane retirement uses the resolved playlist and the Session's current Presenter View, including late Owner views.

use crate::{looking, pane, playlist_in, resolve, showing};
use sessions::{
    SessionId,
    session::{
        Axis, Command, Completion, Doc, Fresh, Input, Layout, Lease, SeatId, SessionState, View,
        ViewerId, Work, restrict, transition,
    },
};
use workspace::store::{DocCommand, HiddenRef, WorkspaceState};

#[test]
fn a_viewer_keeps_its_entry_or_closes_when_it_disappears() {
    let dir = tempfile::tempdir().unwrap();
    let playlist = playlist_in(dir.path(), &["A", "B"]);
    let item = playlist.id;
    let (pending, jobs) = workspace::store::apply(
        &WorkspaceState::default(),
        DocCommand::AddItems {
            items: vec![playlist],
            open: false,
        },
    );
    let (pending, _) = workspace::store::apply(&pending, DocCommand::OpenViewer(item));
    let content = resolve(&jobs[0]).unwrap();
    let (a, b) = (
        content.entries()[0].id.clone(),
        content.entries()[1].id.clone(),
    );
    let viewer = |entry| View {
        viewers: [(
            ViewerId(1),
            sessions::session::ViewerView {
                entry,
                ..looking(item)
            },
        )]
        .into(),
        ..showing(item)
    };
    let shown = |workspace: &WorkspaceState, entry| {
        restrict(
            &viewer(entry),
            &Doc {
                workspace: workspace.clone(),
            },
        )
    };
    assert_eq!(
        shown(&pending, Some(b.clone())).viewers[&ViewerId(1)].entry,
        Some(b.clone())
    );
    assert_eq!(shown(&pending, None).viewers[&ViewerId(1)].entry, None);
    let resolved = workspace::store::complete(&pending, item, jobs[0].attempt, Ok(content));
    assert_eq!(
        shown(&resolved, None).viewers[&ViewerId(1)].entry,
        Some(a.clone())
    );
    let (reloading, jobs) = workspace::store::apply(&resolved, DocCommand::Resolve(item));
    assert_eq!(
        shown(&reloading, Some(b.clone())).viewers[&ViewerId(1)].entry,
        Some(b.clone())
    );

    std::fs::remove_file(dir.path().join("videos/B.mp4")).unwrap();
    let replacement = resolve(&jobs[0]).unwrap();
    let edited = workspace::store::complete(&reloading, item, jobs[0].attempt, Ok(replacement));
    let closed = shown(&edited, Some(b.clone()));
    assert!(closed.viewers.is_empty());
    assert!(closed.layout.is_none());
    assert!(closed.active.is_none());
    assert_eq!(
        shown(&edited, Some(a.clone())).viewers[&ViewerId(1)].entry,
        Some(a.clone())
    );

    let (hidden, _) = workspace::store::apply(
        &resolved,
        DocCommand::SetHidden {
            target: HiddenRef::Entry(a.clone()),
            hidden: true,
        },
    );
    assert_eq!(
        shown(&hidden, None).viewers[&ViewerId(1)].entry,
        Some(b.clone())
    );
    assert_eq!(
        shown(&hidden, Some(a.clone())).viewers[&ViewerId(1)].entry,
        Some(a.clone())
    );
    let (all, _) = workspace::store::apply(
        &hidden,
        DocCommand::SetHidden {
            target: HiddenRef::Entry(b),
            hidden: true,
        },
    );
    assert_eq!(shown(&all, None).viewers[&ViewerId(1)].entry, Some(a));
}

fn command(state: &mut SessionState, seat: SeatId, command: DocCommand) {
    let (next, effects) = transition(
        state,
        Input::Command {
            seat,
            lease: Lease(0),
            command: Command::Content(command),
            fresh: Fresh::now(),
        },
    )
    .unwrap();
    *state = next;
    for spawn in effects.spawn {
        let Work::Resolve(job) = &spawn.work;
        *state = transition(
            state,
            Input::Complete {
                job: spawn.job,
                completion: Completion::Resolved(resolve(job)),
            },
        )
        .unwrap()
        .0;
    }
}

fn view(state: &mut SessionState, seat: SeatId, view: View, seq: u64) {
    *state = transition(
        state,
        Input::View {
            seat,
            lease: Lease(0),
            view,
            seq,
            now_us: 0,
        },
    )
    .unwrap()
    .0;
}

#[test]
fn a_stale_owner_snapshot_does_not_close_another_owners_new_item() {
    let dir = tempfile::tempdir().unwrap();
    let a = crate::video_in(dir.path(), "A.mp4");
    let b = crate::video_in(dir.path(), "B.mp4");
    let (a_id, b_id) = (a.id, b.id);
    let mut state = SessionState::new(SessionId::generate(), false);
    let first = state.seat_owner();
    let second = state.seat_owner();
    command(
        &mut state,
        first,
        DocCommand::AddItems {
            items: vec![a],
            open: true,
        },
    );
    let mut a_only = showing(a_id);
    a_only.viewers.get_mut(&ViewerId(1)).unwrap().pinned = true;
    view(&mut state, first, a_only, 1);
    // Hold Owner 2's clock update from the shared A-only view.
    let mut delayed = (*state.presenter).clone();
    delayed.viewers.get_mut(&ViewerId(1)).unwrap().clock = Some(playback::ClockIntent {
        frame: 7,
        playing: false,
        speed_centi: 100,
        at_us: 0,
    });
    command(
        &mut state,
        first,
        DocCommand::AddItems {
            items: vec![b],
            open: true,
        },
    );
    let mut both = (*state.presenter).clone();
    both.viewers.insert(ViewerId(2), looking(b_id));
    both.layout = Some(Layout::Split {
        axis: Axis::Row,
        share: 5000,
        first: pane(1),
        second: pane(2),
    });
    view(&mut state, first, both.clone(), 2);
    view(&mut state, second, delayed, 1);
    assert!(
        state.doc.workspace.open.contains(&b_id),
        "a stale omission is not a close"
    );
    // The open item can be placed again, as useFrontViewer does after receiving this snapshot.
    view(&mut state, first, both, 3);
    assert_eq!(state.presenter.viewers[&ViewerId(2)].item, b_id);
}

#[test]
fn a_close_during_reload_can_be_undone_by_automatic_placement() {
    let dir = tempfile::tempdir().unwrap();
    let playlist = playlist_in(dir.path(), &["A", "B"]);
    let item = playlist.id;
    let mut state = SessionState::new(SessionId::generate(), false);
    let first = state.seat_owner();
    let second = state.seat_owner();
    command(
        &mut state,
        first,
        DocCommand::AddItems {
            items: vec![playlist],
            open: true,
        },
    );
    let a = crate::entry_key(&state, item, 0).entry;
    let b = crate::entry_key(&state, item, 1).entry;
    let mut both = showing(item);
    both.viewers.get_mut(&ViewerId(1)).unwrap().entry = Some(a.clone());
    both.viewers.insert(
        ViewerId(2),
        sessions::session::ViewerView {
            entry: Some(b),
            ..looking(item)
        },
    );
    both.layout = Some(Layout::Split {
        axis: Axis::Row,
        share: 5000,
        first: pane(1),
        second: pane(2),
    });
    view(&mut state, first, both, 1);
    // Hold Owner 2's close of A before it learns that B disappeared.
    let mut delayed = (*state.presenter).clone();
    delayed.viewers.remove(&ViewerId(1));
    delayed.active = Some(ViewerId(2));
    delayed.layout = Some(*pane(2));
    std::fs::remove_file(dir.path().join("videos/B.mp4")).unwrap();
    command(&mut state, first, DocCommand::Resolve(item));
    view(&mut state, second, delayed, 1);
    assert!(state.doc.workspace.open.contains(&item));
    assert!(state.presenter.viewers.is_empty());
    view(&mut state, first, showing(item), 2);
    assert_eq!(state.presenter.viewers[&ViewerId(1)].entry, Some(a));
}
