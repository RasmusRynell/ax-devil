//! Pane retirement uses the resolved playlist and the Session's current Presenter View, including late Owner views.

use crate::{looking, pane, playlist_in, resolve, showing};
use sessions::{
    SessionId,
    session::{
        Axis, Command, Completion, Doc, Fresh, Input, Layout, Lease, SeatId, SessionState, View,
        ViewerId, Work, restrict, transition,
    },
};
use workspace_store::{DocCommand, HiddenRef, WorkspaceState};

#[test]
fn a_viewer_keeps_its_entry_or_closes_when_it_disappears() {
    let dir = tempfile::tempdir().unwrap();
    let playlist = playlist_in(dir.path(), &["A", "B"]);
    let item = playlist.id;
    let (pending, jobs) = workspace_store::apply(
        &WorkspaceState::default(),
        DocCommand::AddItems {
            items: vec![playlist],
            open: false,
        },
    );
    let (pending, _) = workspace_store::apply(&pending, DocCommand::OpenViewer(item));
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
    let resolved = workspace_store::complete(&pending, item, jobs[0].attempt, Ok(content));
    assert_eq!(
        shown(&resolved, None).viewers[&ViewerId(1)].entry,
        Some(a.clone())
    );
    let (reloading, jobs) = workspace_store::apply(&resolved, DocCommand::Resolve(item));
    assert_eq!(
        shown(&reloading, Some(b.clone())).viewers[&ViewerId(1)].entry,
        Some(b.clone())
    );

    std::fs::remove_file(dir.path().join("videos/B.mp4")).unwrap();
    let replacement = resolve(&jobs[0]).unwrap();
    let edited = workspace_store::complete(&reloading, item, jobs[0].attempt, Ok(replacement));
    let closed = shown(&edited, Some(b.clone()));
    assert!(closed.viewers.is_empty());
    assert!(closed.layout.is_none());
    assert!(closed.active.is_none());
    assert_eq!(
        shown(&edited, Some(a.clone())).viewers[&ViewerId(1)].entry,
        Some(a.clone())
    );

    let (hidden, _) = workspace_store::apply(
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
    let (all, _) = workspace_store::apply(
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
fn a_late_owner_close_cannot_reopen_an_entry_retired_by_reload() {
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
    let (a, b) = (
        crate::entry_key(&state, item, 0).entry,
        crate::entry_key(&state, item, 1).entry,
    );
    view(
        &mut state,
        first,
        View {
            viewers: [
                (
                    ViewerId(1),
                    sessions::session::ViewerView {
                        entry: Some(a),
                        pinned: true,
                        ..looking(item)
                    },
                ),
                (
                    ViewerId(2),
                    sessions::session::ViewerView {
                        entry: Some(b),
                        ..looking(item)
                    },
                ),
            ]
            .into(),
            layout: Some(Layout::Split {
                axis: Axis::Row,
                share: 4000,
                first: pane(1),
                second: pane(2),
            }),
            ..showing(item)
        },
        1,
    );
    // Owner 2 closes A using its old A/B view, but its packet waits until after Owner 1's reload.
    let mut delayed = (*state.presenter).clone();
    delayed.viewers.remove(&ViewerId(1));
    delayed.active = Some(ViewerId(2));
    delayed.layout = Some(*pane(2));
    let survivor = state.presenter.viewers[&ViewerId(1)].clone();
    std::fs::remove_file(dir.path().join("videos/B.mp4")).unwrap();
    command(&mut state, first, DocCommand::Resolve(item));
    assert_eq!(state.presenter.viewers, [(ViewerId(1), survivor)].into());
    assert!(state.doc.workspace.open.contains(&item));

    view(&mut state, second, delayed, 1);
    assert!(
        !state.doc.workspace.open.contains(&item),
        "no open item remains for a window to auto-place"
    );
    assert!(state.presenter.viewers.is_empty());
    assert!(state.presenter.layout.is_none());
    // A stale client can still try to auto-place A; only an explicit Open can reopen its item.
    view(&mut state, first, showing(item), 2);
    assert!(state.presenter.viewers.is_empty());
    assert!(state.doc.workspace.open.is_empty());
}
