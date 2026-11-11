//! A viewport and the overlays change only through their own commands, applied to the view as the server has it now.

use crate::{Rig, looking, pane, showing};
use sessions::{
    CursorOptions, SeatMode,
    session::{
        Access, Axis, Command, Control, Layout, Overlay, Overlays, Refusal, View, ViewerId,
        Viewport,
    },
};
use workspace_store::DocCommand;

const ZOOMED: Viewport = Viewport {
    x: 0.25,
    y: 0.5,
    w: 0.5,
    h: 0.25,
};

fn zoom(viewport: Viewport) -> Command {
    Command::SetViewport {
        viewer: ViewerId(1),
        viewport: Some(viewport),
    }
}

#[tokio::test(start_paused = true)]
async fn a_viewport_never_reverts_another_owner_seats_view_nor_is_reverted_by_it() {
    let rig = Rig::new();
    let (first, _) = rig.shared(Access::Watch);
    let (item, _) = rig.open_video(&first, "a.mp4");
    let second = rig
        .service
        .attach(first.session(), true, CursorOptions::default())
        .unwrap();
    let other = rig.video("b.mp4");
    let other_id = other.id;

    // The first seat opens a second viewer; the second seat then zooms, from a copy that does not have it.
    first
        .command(Command::Content(DocCommand::AddItems {
            items: vec![other],
            open: true,
        }))
        .unwrap();
    rig.jobs.finish_all();
    let both = View {
        viewers: [
            (ViewerId(1), looking(item)),
            (ViewerId(2), looking(other_id)),
        ]
        .into(),
        layout: Some(Layout::Split {
            axis: Axis::Row,
            share: 5000,
            first: pane(1),
            second: pane(2),
        }),
        ..showing(item)
    };
    first.view(both, 2).unwrap();
    second.command(zoom(ZOOMED)).unwrap();
    let presenter = second.state().presenter.clone();
    assert_eq!(presenter.viewers.len(), 2, "the layout change stays");
    assert_eq!(presenter.viewers[&ViewerId(1)].viewport, Some(ZOOMED));
    assert_eq!(second.state().presenter_by, Some(second.seat()));

    // A view sent whole from a copy without the zoom does not take it back.
    first.view(showing(item), 3).unwrap();
    assert_eq!(
        first.state().presenter.viewers[&ViewerId(1)].viewport,
        Some(ZOOMED)
    );
}

#[tokio::test(start_paused = true)]
async fn a_guest_that_may_only_watch_cannot_set_a_viewport_and_an_exploring_one_keeps_its_own() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    rig.open_video(&owner, "a.mp4");
    assert_eq!(guest.command(zoom(ZOOMED)).err(), Some(Refusal::NotAllowed));

    owner
        .command(Command::Control(Control::SetAccess {
            access: Access::Explore,
        }))
        .unwrap();
    guest.command(zoom(ZOOMED)).unwrap();
    let state = guest.state();
    assert_eq!(state.seats[&guest.seat()].mode(), SeatMode::Exploring);
    assert_eq!(state.presenter.viewers[&ViewerId(1)].viewport, None);

    // The presenter zooms elsewhere and the guest's own view keeps its zoom.
    let elsewhere = Viewport { x: 0.0, ..ZOOMED };
    owner.command(zoom(elsewhere)).unwrap();
    let state = guest.state();
    let own = state.seats[&guest.seat()].view.as_ref().unwrap();
    assert_eq!(own.viewers[&ViewerId(1)].viewport, Some(ZOOMED));
    assert_eq!(
        state.presenter.viewers[&ViewerId(1)].viewport,
        Some(elsewhere)
    );
}

#[tokio::test(start_paused = true)]
async fn a_session_starts_with_the_overlays_given_and_a_toggle_changes_the_current_view_only() {
    let rig = Rig::new();
    let start = Overlays {
        object_details: false,
        lane_names: true,
    };
    let owner = rig
        .service
        .create(true, CursorOptions::default(), start)
        .unwrap();
    assert_eq!(owner.state().presenter.overlays, start);
    owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    let guest = rig.join(&owner);
    let (item, _) = rig.open_video(&owner, "a.mp4");
    let hide_names = Command::SetOverlay {
        overlay: Overlay::LaneNames,
        shown: false,
    };

    // The toggle lands in the Presenter View and is stamped; a view sent whole, from a copy without it, keeps it.
    let rev = owner.state().presenter_rev;
    owner.command(hide_names.clone()).unwrap();
    let state = owner.state();
    assert_eq!(
        state.presenter.overlays,
        Overlays {
            object_details: false,
            lane_names: false
        }
    );
    assert_eq!(
        (state.presenter_rev, state.presenter_by),
        (rev + 1, Some(owner.seat()))
    );
    owner.view(showing(item), 2).unwrap();
    assert!(!owner.state().presenter.overlays.lane_names);

    // A Guest that may only watch is refused; one that may explore changes its own view and not the presenter's.
    assert_eq!(
        guest.command(hide_names.clone()).err(),
        Some(Refusal::NotAllowed)
    );
    owner
        .command(Command::Control(Control::SetAccess {
            access: Access::Explore,
        }))
        .unwrap();
    guest
        .command(Command::SetOverlay {
            overlay: Overlay::LaneNames,
            shown: true,
        })
        .unwrap();
    let state = guest.state();
    let own = state.seats[&guest.seat()].view.as_ref().unwrap();
    assert!(own.overlays.lane_names);
    assert!(!state.presenter.overlays.lane_names);
}
