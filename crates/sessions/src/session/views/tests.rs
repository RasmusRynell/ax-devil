use super::{super::layout::Axis, *};
use serde_json::json;

#[test]
fn a_view_reads_as_the_wire_writes_it_and_an_id_that_names_no_item_is_refused() {
    let (front, behind) = (ItemId::generate(), ItemId::generate());
    let message = json!({
        "sidebar": { "view": "sessions", "open": false },
        "active": 2,
        "viewers": {
            "1": { "item": behind.to_string(), "pinned": false },
            "2": {
                "item": front.to_string(),
                "pinned": true,
                "clock": { "frame": 7, "playing": true, "speedCenti": 150, "atUs": -3 },
            },
        },
        "layout": {
            "type": "split", "axis": "row", "share": 3000,
            "first": { "type": "pane", "viewer": 1 },
            "second": { "type": "pane", "viewer": 2 },
        },
        "overlays": { "objectDetails": true, "laneNames": false },
    });
    let view: View = serde_json::from_str(&message.to_string()).unwrap();
    assert_eq!(view.sidebar.view, SidebarView::Sessions);
    assert_eq!(view.active, Some(ViewerId(2)));
    assert_eq!(view.viewers[&ViewerId(1)].item, behind);
    assert!(view.viewers[&ViewerId(2)].pinned);
    assert_eq!(view.viewers[&ViewerId(2)].clock.unwrap().speed_centi, 150);
    assert!(!view.overlays.lane_names);
    assert_eq!(serde_json::to_value(&view).unwrap(), message);

    let mut bad = message;
    bad["viewers"]["1"]["item"] = json!("not an item");
    assert!(serde_json::from_value::<View>(bad).is_err());
}

fn pane(viewer: u32) -> Box<Layout> {
    Box::new(Layout::Pane {
        viewer: ViewerId(viewer),
    })
}

fn looking(item: ItemId) -> ViewerView {
    ViewerView {
        item,
        pinned: false,
        clock: None,
        entry: None,
        viewport: None,
    }
}

#[test]
fn restricting_drops_the_viewers_of_closed_items_and_unplaced_ones_and_keeps_the_other_shares() {
    let video = workspace::intake::video(
        std::path::Path::new("v.mp4"),
        &[],
        &handlers::Handlers::built_in(),
    )
    .unwrap();
    let (open, closed) = (video.id, ItemId::generate());
    let (workspace, _) = workspace::store::apply(
        &WorkspaceState::default(),
        workspace::store::DocCommand::AddItems {
            items: vec![video],
            open: false,
        },
    );
    let (workspace, _) =
        workspace::store::apply(&workspace, workspace::store::DocCommand::OpenViewer(open));
    let split = |axis, share, first, second| Layout::Split {
        axis,
        share,
        first,
        second,
    };
    // Two panes show the open item, one the closed item, and viewer 4 is in no pane.
    let view = View {
        active: Some(ViewerId(2)),
        viewers: (1..=4)
            .map(|id| (ViewerId(id), looking(if id == 2 { closed } else { open })))
            .collect(),
        layout: Some(split(
            Axis::Row,
            3000,
            pane(1),
            Box::new(split(Axis::Column, 7000, pane(2), pane(3))),
        )),
        ..View::default()
    };
    let restricted = restrict(&view, &Doc { workspace });
    assert_eq!(
        restricted.viewers.keys().copied().collect::<Vec<_>>(),
        [ViewerId(1), ViewerId(3)]
    );
    assert_eq!(restricted.active, None);
    assert_eq!(
        restricted.layout,
        Some(split(Axis::Row, 3000, pane(1), pane(3)))
    );
}
